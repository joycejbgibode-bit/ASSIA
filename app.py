import os
import time
import base64
import urllib.parse
import unicodedata
import streamlit as st
from google import genai
from google.genai import types
from pypdf import PdfReader
from fpdf import FPDF

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(
    page_title="ASSIA",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- INJECTION PWA, HEAD HTML & ICÔNE BASE64 ---
st.markdown("""
<link rel="manifest" href="app/static/manifest.json">
<link rel="apple-touch-icon" href="app/static/icon-192.png">
<meta name="theme-color" content="#093c39">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ASSIA">
""", unsafe_allow_html=True)

# --- FONCTION D'EXÉCUTION JS SÉCURISÉE (ST.IFRAME) ---
def executer_js(script_js, height='content'):
    """Exécute du code JavaScript de manière sécurisée via st.iframe en sécurisant la hauteur."""
    if height == 0 or height is None:
        height = 'content'
    html_content = f"<html><body><script>{script_js}</script></body></html>"
    encoded = urllib.parse.quote(html_content)
    st.iframe(f"data:text/html;charset=utf-8,{encoded}", height=height)

# --- ENREGISTREMENT DU SERVICE WORKER ---
executer_js("""
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function() {
      navigator.serviceWorker.register('sw.js').then(function(registration) {
        console.log('ServiceWorker enregistré avec succès scope: ', registration.scope);
      }, function(err) {
        console.log('Échec de l\'enregistrement du ServiceWorker: ', err);
      });
    });
  }
""", height='content')

# --- LOCALISATION DES DOSSIERS DE DOCUMENTS ET IMAGES ---
REPERTOIRE_SCRIPT = os.path.dirname(os.path.abspath(__file__))
DOSSIER_ANATOMIE = os.path.join(REPERTOIRE_SCRIPT, "docs", "anatomie")
DOSSIER_INSTRUMENTS = os.path.join(REPERTOIRE_SCRIPT, "docs", "instruments")
DOSSIER_DOCS = os.path.join(REPERTOIRE_SCRIPT, "docs")

def obtenir_gif_base64(nom_fichier="loading.gif"):
    """Convertit le GIF local en chaîne base64 pour un affichage HTML garanti."""
    chemin_gif = os.path.join(REPERTOIRE_SCRIPT, nom_fichier)
    if os.path.exists(chemin_gif):
        try:
            with open(chemin_gif, "rb") as f:
                data = f.read()
            return base64.b64encode(data).decode("utf-8")
        except Exception:
            pass
    return None

def obtenir_logo_base64(nom_fichier="icone_detoure.png"):
    """Convertit l'image du logo local en chaîne base64 pour un affichage HTML garanti."""
    chemin_img = os.path.join(REPERTOIRE_SCRIPT, nom_fichier)
    if os.path.exists(chemin_img):
        try:
            with open(chemin_img, "rb") as f:
                data = f.read()
            return base64.b64encode(data).decode("utf-8")
        except Exception:
            pass
    return None

def lister_images_dossier(dossier):
    """Récupère la liste exacte de tous les fichiers images d'un dossier donné."""
    if not os.path.exists(dossier):
        return []
    extensions = (".png", ".jpg", ".jpeg", ".webp")
    return [f for f in os.listdir(dossier) if f.lower().endswith(extensions)]

def normaliser_texte(texte):
    """Supprime accents, majuscules et uniformise les séparateurs pour une comparaison souple."""
    texte = unicodedata.normalize('NFD', texte).encode('ascii', 'ignore').decode('utf-8')
    texte = texte.replace('-', ' ').replace('_', ' ')
    return texte.lower()

# --- GÉNÉRATION DE PDF ---
def creer_pdf(texte):
    """Génère un PDF à partir du texte de la réponse."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=11)
    
    texte_propre = texte.replace('⚡', '').replace('⏱️', '').replace('🛏️', '').replace('🫀', '').replace('📋', '').replace('🖼️', '').replace('🩺', '').replace('📥', '').replace('🔍', '')
    texte_propre = texte_propre.encode('latin-1', 'replace').decode('latin-1')
    
    pdf.multi_cell(0, 7, txt=texte_propre)
    
    try:
        return bytes(pdf.output(dest='S'), 'latin-1')
    except TypeError:
        return bytes(pdf.output())

def est_requete_purement_materiel(requete_utilisateur, filtres_actifs):
    """Détecte si la requête concerne du matériel général ou un emplacement."""
    if "MATÉRIEL" in filtres_actifs and "ANATOMIE" not in filtres_actifs and "RAPPEL COMPLET" not in filtres_actifs:
        return True

    mots_cles_materiel = [
        "tombe", "trouver", "chercher", "boite", "armoire", "tiroir", "reserve", "dms",
        "dmuu", "dmrs", "chariot", "panier", "ou est", "ou se trouve", "remplacer",
        "combien", "table", "tables", "quantite"
    ]
    req_norm = normaliser_texte(requete_utilisateur)
    for mc in mots_cles_materiel:
        if mc in req_norm:
            return True
    return False

def trouver_schemas_locaux(requete_utilisateur, filtres_actifs):
    """
    ROUTAGE EXPLICITE ET RECHERCHE D'INSTRUMENTS AVEC ESPACES OU ACCENTS.
    """
    req_norm = normaliser_texte(requete_utilisateur)
    
    est_rappel = "RAPPEL COMPLET" in filtres_actifs or "rappel complet" in req_norm or "complet" in req_norm
    est_anatomie = "ANATOMIE" in filtres_actifs or "anatomie" in req_norm or "vascularisation" in req_norm or "montre" in req_norm
    
    mots_demande_visuelle = ["montre", "montre-moi", "montre moi", "vois", "voir", "illustration", "image"]
    veut_voir_instrument = any(mdv in req_norm for mdv in mots_demande_visuelle)

    images_trouvees = []
    chemins_vus = set()

    # --- 1. RECHERCHE D'INSTRUMENTS DANS DOSSIER_INSTRUMENTS ---
    fichiers_instruments = lister_images_dossier(DOSSIER_INSTRUMENTS)
    if fichiers_instruments and (veut_voir_instrument or "instrument" in req_norm or "pince" in req_norm or "ciseaux" in req_norm or "ecarteur" in req_norm or "bistouri" in req_norm or "différence" in req_norm):
        for nom_f in fichiers_instruments:
            nom_base = normaliser_texte(os.path.splitext(nom_f)[0])
            mots_instrument = nom_base.split()
            
            if all(mot in req_norm for mot in mots_instrument) and len(mots_instrument) > 0:
                chemin_complet = os.path.join(DOSSIER_INSTRUMENTS, nom_f)
                if chemin_complet not in chemins_vus:
                    images_trouvees.append({
                        "fichier": chemin_complet,
                        "titre": nom_base.title(),
                        "type": "instrument"
                    })
                    chemins_vus.add(chemin_complet)

    # --- 2. ROUTAGE ANATOMIQUE (DPC, Appendice, Colons) ---
    if est_rappel or est_anatomie:
        if not est_requete_purement_materiel(requete_utilisateur, filtres_actifs):
            fichiers_dispos = lister_images_dossier(DOSSIER_ANATOMIE)
            if fichiers_dispos:
                routages_explicites = {
                    "dpc": "dpc",
                    "duodeno-pancreatectomie": "dpc",
                    "duodeno pancreatectomie": "dpc",
                    "duodenopancreatectomie": "dpc",
                    "appendicectomie": "appendice",
                    "appendice": "appendice",
                    "colectomie droite": "colon_droit",
                    "colon droit": "colon_droit",
                    "colectomie gauche": "colon_gauche",
                    "colon gauche": "colon_gauche"
                }

                organe_cible = None
                for cle, val in routages_explicites.items():
                    if cle in req_norm:
                        organe_cible = val
                        break
                        
                if organe_cible:
                    st.session_state.dernier_organe_connu = organe_cible
                elif st.session_state.get("dernier_organe_connu"):
                    organe_cible = st.session_state.dernier_organe_connu
                else:
                    for msg in reversed(st.session_state.get("chat_display", [])):
                        msg_norm = normaliser_texte(msg["content"])
                        for cle, val in routages_explicites.items():
                            if cle in msg_norm:
                                organe_cible = val
                                st.session_state.dernier_organe_connu = organe_cible
                                break
                        if organe_cible:
                            break

                if organe_cible:
                    fichiers_cibles = []
                    if organe_cible == "dpc":
                        fichiers_cibles = ["schema dpc"]
                    elif organe_cible == "appendice":
                        fichiers_cibles = ["schema appendice"]
                    elif organe_cible == "colon_droit":
                        fichiers_cibles = ["schema colon droit", "vascularisation arterielle colon droit", "vascularisation veineuse colon droit"] if est_anatomie else ["schema colon droit"]
                    elif organe_cible == "colon_gauche":
                        fichiers_cibles = ["schema colon gauche", "vascularisation arterielle colon gauche", "vascularisation veineuse colon gauche"] if est_anatomie else ["schema colon gauche"]

                    for nom_f in fichiers_dispos:
                        nom_base = normaliser_texte(os.path.splitext(nom_f)[0])
                        for cible in fichiers_cibles:
                            mots_cible = normaliser_texte(cible).split()
                            if all(mot in nom_base for mot in mots_cible):
                                chemin_complet = os.path.join(DOSSIER_ANATOMIE, nom_f)
                                if chemin_complet not in chemins_vus:
                                    images_trouvees.append({
                                        "fichier": chemin_complet,
                                        "titre": nom_base.title(),
                                        "type": "anatomie"
                                    })
                                    chemins_vus.add(chemin_complet)

    return images_trouvees

def afficher_contenu_complet(texte, images, est_rappel_complet):
    if isinstance(images, dict):
        liste_img = images.get("principale", []) + images.get("def_abord", []) + images.get("mepc", []) + images.get("temps", [])
    elif isinstance(images, list):
        liste_img = images
    else:
        liste_img = []

    est_scinde = est_rappel_complet and bool(liste_img)
    
    if est_scinde:
        col_texte, col_images = st.columns([1.6, 1.0], gap="large")
        with col_texte:
            st.markdown(texte)
        with col_images:
            st.markdown('<div class="schemas-panel-title">🖼️ Illustration & Schéma</div>', unsafe_allow_html=True)
            for img in liste_img:
                if isinstance(img, dict) and "fichier" in img and os.path.exists(img["fichier"]):
                    st.image(img["fichier"], caption=f"{img.get('titre', '')}", use_container_width=True)
    else:
        st.markdown(texte)
        if liste_img:
            st.write("---")
            for img in liste_img:
                if isinstance(img, dict) and "fichier" in img and os.path.exists(img["fichier"]):
                    st.image(img["fichier"], caption=f"🖼️ {img.get('titre', '')}", use_container_width=True)

# --- FEUILLE DE STYLE UI MÉDICALE ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700&family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap');

    html, body, .stMarkdown p, .stMarkdown li {
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
        color: #0f2e2d !important;
    }

    h1, h2, h3, h4, .app-font-title {
        font-family: 'Outfit', sans-serif !important;
        color: #093c39 !important;
    }

    strong, b {
        color: #062b28 !important;
        font-weight: 700 !important;
    }

    .main .block-container {
        max-width: 98% !important;
        padding: 1.5rem !important;
    }

    .stApp {
        background-color: #f8fafc;
        background-image: 
            radial-gradient(at 0% 0%, rgba(11, 69, 66, 0.05) 0px, transparent 50%),
            radial-gradient(at 100% 100%, rgba(217, 119, 6, 0.04) 0px, transparent 50%),
            radial-gradient(#cbd5e1 0.7px, transparent 0.7px);
        background-size: 100% 100%, 100% 100%, 20px 20px;
    }

    .home-header {
        background: linear-gradient(135deg, #093c39 0%, #0e544f 50%, #156b64 100%);
        padding: 1.8rem 2rem;
        border-radius: 20px;
        color: white;
        margin-bottom: 1.8rem;
        box-shadow: 0 12px 28px -6px rgba(9, 60, 57, 0.28);
        border: 1px solid rgba(255, 255, 255, 0.12);
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 24px;
        text-align: center;
    }

    .home-header h1 {
        color: #ffffff !important;
        font-size: 2.2rem;
        margin: 0;
    }

    .home-header p {
        color: #bbf7d0 !important;
        font-size: 0.96rem;
        margin-top: 0.35rem;
        margin-bottom: 0;
    }

    section[data-testid="stSidebar"] {
        background-color: #f7f4ed !important;
        border-right: 1.5px solid #e3dacb !important;
    }

    section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] {
        background-color: #f7f4ed !important;
    }

    [data-testid="stSidebarCollapseButton"] button,
    button[kind="headerNoPadding"] {
        color: #0f2e2d !important;
        background-color: #ede5d8 !important;
        border: 1px solid #d4c7b5 !important;
        border-radius: 10px !important;
        transition: all 0.2s ease !important;
    }

    [data-testid="stSidebarCollapseButton"] button:hover,
    button[kind="headerNoPadding"]:hover {
        background-color: #093c39 !important;
        color: #ffffff !important;
        border-color: #093c39 !important;
    }

    .st-nav-btn button {
        width: 100% !important;
        background-color: #ede7dc !important;
        border: 1.5px solid #d8cebe !important;
        border-radius: 14px !important;
        padding: 0.75rem 1rem !important;
        margin-bottom: 0.9rem !important;
        color: #0f2e2d !important;
        font-weight: 600 !important;
        text-align: left !important;
        justify-content: flex-start !important;
        box-shadow: 0 2px 5px rgba(0, 0, 0, 0.02) !important;
        transition: all 0.2s ease-in-out !important;
    }

    .st-nav-btn button:hover {
        background-color: #e6f4f1 !important;
        border-color: #0d5c58 !important;
        color: #0d5c58 !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 4px 10px rgba(13, 92, 88, 0.1) !important;
    }

    .st-nav-btn-active button {
        width: 100% !important;
        background: linear-gradient(135deg, #093c39 0%, #115e59 100%) !important;
        border: 1.5px solid #093c39 !important;
        border-radius: 14px !important;
        padding: 0.75rem 1rem !important;
        margin-bottom: 0.9rem !important;
        color: #ffffff !important;
        font-weight: 700 !important;
        text-align: left !important;
        justify-content: flex-start !important;
        box-shadow: 0 6px 14px rgba(9, 60, 57, 0.18) !important;
    }

    .sidebar-bottom-spacer {
        height: 38vh;
    }

    .app-workspace {
        background: rgba(255, 255, 255, 0.9);
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 1.1rem 1.4rem;
        margin-bottom: 1.2rem;
    }

    .filter-label {
        font-size: 0.95rem;
        font-weight: 700;
        color: #093c39 !important;
        text-transform: uppercase;
        letter-spacing: 0.6px;
        margin-bottom: 4px;
    }

    div[data-testid="stCheckbox"] {
        background: #ffffff;
        border: 1.5px solid #cbd5e1;
        border-radius: 14px;
        padding: 12px 14px;
        min-height: 52px;
    }

    div[data-testid="stCheckbox"]:hover {
        border-color: #0d5c58;
        background-color: #f0fdfa;
    }

    div[data-testid="stCheckbox"] label p {
        color: #0f2e2d !important;
        font-weight: 600 !important;
    }

    div[data-testid="stChatMessage"] {
        border-radius: 18px !important;
        padding: 1.2rem 1.6rem !important;
        margin-bottom: 1.2rem !important;
    }

    div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
        background-color: #edf2f7 !important;
        border: 1.5px solid #cbd5e1 !important;
    }

    div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
        background-color: #fcfbf7 !important;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180' viewBox='0 0 180 180'%3E%3Cg fill='none' stroke='%238c7b64' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round' opacity='0.16'%3E%3Cpath d='M25 8v34M18 16c6-4 11 3 5 8s-5 8 5 8'/%3E%3Cpath d='M95 12l16 16-24 24-8-4-3-8 19-28zM76 48l-10 10c-2 2-4 5-4 5s3-1 5-4l9-11'/%3E%3Crect x='132' y='16' width='28' height='16' rx='2.5'/%3E%3Cpath d='M132 20l-10-4M160 20l10-4M132 28l-10 4M160 28l10 4M138 24h16'/%3E%3Crect x='20' y='65' width='20' height='22' rx='2'/%3E%3Cpath d='M30 70v6M27 73h6M26 83h6v-4h-6v4z'/%3E%3Crect x='85' y='72' width='32' height='14' rx='7' transform='rotate(18 85 72)'/%3E%3Ccircle cx='101' cy='79' r='1.2'/%3E%3Ccircle cx='96' cy='77' r='1.2'/%3E%3Ccircle cx='106' cy='81' r='1.2'/%3E%3Crect x='32' y='128' width='24' height='9' rx='1.5' transform='rotate(-40 32 128)'/%3E%3Cpath d='M24 146l-6 6M18 152l-3 3M43 118l8-8M49 112l6 6'/%3E%3Cpath d='M130 118c0-3 2.5-4 4.5-4s4.5 1 4.5 4v9c0 1.5 1.5 2.5 2.5 2.5s2.5-1 2.5-2.5v-7c0-2.5 1.5-3.5 3.5-3.5s3.5 1 3.5 3.5v10c0 7-4 12-11 12h-2c-6 0-8-4-8-8v-11c0-2.5 1.5-3.5 3.5-3.5s3.5 1 3.5 3.5v7'/%3E%3C/g%3E%3C/svg%3E") !important;
        background-repeat: repeat !important;
        border: 1.5px solid #e7ded0 !important;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.03) !important;
    }

    [data-testid*="Icon"], .material-symbols-rounded, .material-icons {
        font-family: 'Material Symbols Rounded', 'Material Icons' !important;
    }

    .schemas-panel-title {
        font-family: 'Outfit', sans-serif;
        font-size: 1.05rem;
        font-weight: 700;
        color: #093c39 !important;
        border-bottom: 2px solid #0d5c58;
        padding-bottom: 6px;
        margin-bottom: 12px;
    }

    .btn-relancer button {
        background: #f0fdfa !important;
        border: 1.5px solid #0d5c58 !important;
        border-radius: 12px !important;
        color: #093c39 !important;
        padding: 4px 10px !important;
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 6px !important;
        box-shadow: 0 2px 6px rgba(13, 92, 88, 0.1) !important;
        transition: all 0.2s ease !important;
    }
    .btn-relancer button:hover {
        background: #093c39 !important;
        color: #ffffff !important;
        transform: translateY(-2px);
    }
</style>
""", unsafe_allow_html=True)

# --- CHARGEMENT DU DOSSIER DE DOCUMENTS ---
@st.cache_resource
def charger_connaissances(racine):
    corpus = ""
    if os.path.exists(racine):
        for root, _, files in os.walk(racine):
            if "anatomie" in root or "instruments" in root:
                continue
            for fichier in files:
                chemin = os.path.join(root, fichier)
                if fichier.endswith(".pdf"):
                    try:
                        reader = PdfReader(chemin)
                        for page in reader.pages:
                            corpus += f"\n[SOURCE: {fichier}]\n" + (page.extract_text() or "")
                    except Exception:
                        pass
                elif fichier.endswith(".txt"):
                    try:
                        with open(chemin, "r", encoding="utf-8") as f:
                            corpus += f"\n[SOURCE: {fichier}]\n" + f.read()
                    except Exception:
                        pass
    return corpus

contenu_fiches = charger_connaissances(DOSSIER_DOCS)

# Récupération de la liste des instruments disponibles pour informer l'IA
fichiers_instruments_dispos = lister_images_dossier(DOSSIER_INSTRUMENTS)
liste_noms_instruments = [os.path.splitext(f)[0].replace('-', ' ').replace('_', ' ') for f in fichiers_instruments_dispos]
consigne_instruments_str = f"Instruments disponibles en image dans ta base : {', '.join(liste_noms_instruments) if liste_noms_instruments else 'Aucun pour le moment'}."

# --- DIRECTIVES SYSTÈME OPÉRATOIRES & INITIATIVE INSTRUMENTS ---
SYSTEM_INSTRUCTION = f"""
# RÔLE ET MISSION
Tu es ASSIA, un assistant technique d'aide en situation opérationnelle au bloc opératoire. Tu accompagnes les professionnels titulaires, les agents en intégration et les intérimaires.

---

# INITIATIVE SPONTANÉE VIS-À-VIS DES INSTRUMENTS
- {consigne_instruments_str}
- Lorsque tu mentionnes un instrument dans tes explications (ou lors de comparaisons/rappels), si et seulement si cet instrument fait partie de la liste ci-dessus, tu dois proposer spontanément à l'utilisateur de l'afficher (ex: "Voulez-vous que je vous montre une illustration de la pince DeBakey ?"). 
- Ne propose JAMAIS de montrer un instrument s'il ne figure pas dans la liste ci-dessus.

---

# INTERDICTION FORMELLE DES SCHÉMAS EN TEXTE (ASCII ART)
- NE JAMAIS tenter de dessiner, de tracer ou de représenter visuellement un organe ou un instrument avec du texte. Ton rôle est UNIQUEMENT textuel.

---

# RÈGLE D'UTILISATION DES SOURCES EXTERNES & RECHERCHE WEB
- Si une information ne se trouve pas dans la base documentaire interne, tu ne dois jamais lancer de recherche autonome ou inventer de réponse.
- Tu dois impérativement demander l'autorisation à l'utilisateur avant d'élargir la recherche ou de consulter une source externe.

---

# SOURCES ET TRAÇABILITÉ OBLIGATOIRE
1. Protocoles et matériel internes : `[Source : Base documentaire interne]`
2. Questions générales ou culture professionnelle (si validé par l'utilisateur) : `[Source : Recherche externe / Connaissance générale]`

---
{contenu_fiches}
---
"""

if "historique_requetes" not in st.session_state:
    st.session_state.historique_requetes = []

if "menu_navigation" not in st.session_state:
    st.session_state.menu_navigation = "💬 Assistant Opératoire"

if "derniere_requete" not in st.session_state:
    st.session_state.derniere_requete = None

if "relance_demandee" not in st.session_state:
    st.session_state.relance_demandee = False

if "retracter_sidebar" not in st.session_state:
    st.session_state.retracter_sidebar = False
    
if "dernier_organe_connu" not in st.session_state:
    st.session_state.dernier_organe_connu = None

if st.session_state.retracter_sidebar:
    executer_js("""
        const btnFermer = window.parent.document.querySelector('[data-testid="stSidebarCollapseButton"] button') ||
                          window.parent.document.querySelector('button[kind="headerNoPadding"]');
        if (btnFermer) {
            btnFermer.click();
        }
    """, height='content')
    st.session_state.retracter_sidebar = False

# --- BARRE LATÉRALE ---
with st.sidebar:
    st.markdown("<p style='font-size:0.75rem; font-weight:700; color:#093c39; letter-spacing:1px; margin-bottom:14px;'>NAVIGATION</p>", unsafe_allow_html=True)
    
    classe_btn1 = "st-nav-btn-active" if st.session_state.menu_navigation == "💬 Assistant Opératoire" else "st-nav-btn"
    st.markdown(f'<div class="{classe_btn1}">', unsafe_allow_html=True)
    if st.button("💬  Assistant Opératoire", key="nav_assistant"):
        st.session_state.menu_navigation = "💬 Assistant Opératoire"
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    classe_btn2 = "st-nav-btn-active" if st.session_state.menu_navigation == "📜 Historique des requêtes" else "st-nav-btn"
    st.markdown(f'<div class="{classe_btn2}">', unsafe_allow_html=True)
    if st.button("📜  Historique des requêtes", key="nav_historique"):
        st.session_state.menu_navigation = "📜 Historique des requêtes"
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-bottom-spacer"></div>', unsafe_allow_html=True)
    st.divider()

    st.markdown("<p style='font-size:0.8rem; font-weight:700; color:#093c39; margin-bottom:6px;'>MOTEUR D'INTELLIGENCE</p>", unsafe_allow_html=True)
    
    modele_choisi = st.selectbox(
        "Modèle IA actif",
        options=["Automatique", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash"],
        index=0,
        help="Modèles officiels et stables. Historique synchronisé en continu."
    )

    st.divider()
    st.markdown("<p style='font-size:0.8rem; font-weight:700; color:#093c39; margin-bottom:2px;'>SÉCURITÉ & ACCÈS</p>", unsafe_allow_html=True)
    st.caption("🔒 Session chiffrée — Données strictement internes.")

# --- GESTION DES CLÉS MULTIPLES ---
try:
    cle_1 = st.secrets.get("GEMINI_API_KEY_1") or st.secrets.get("GEMINI_API_KEY")
    cle_2 = st.secrets.get("GEMINI_API_KEY_2")
    cle_3 = st.secrets.get("GEMINI_API_KEY_3")
except Exception:
    cle_1 = os.getenv("GEMINI_API_KEY")
    cle_2 = None
    cle_3 = None

cles_disponibles = [k for k in [cle_1, cle_2, cle_3] if k]

if not cles_disponibles:
    st.error("⚠️ Clé API introuvable dans `.streamlit/secrets.toml`.")
    st.stop()

# --- GÉNÉRATION AVEC SYNCHRONISATION ABSOLUE DE L'HISTORIQUE ---
def generer_avec_memoire_chat(instruction_filtre, modele_prefere, cles, historique_ui):
    modeles_a_tenter = ["gemini-3.8-flash", "gemini-3.6-flash"] if "Automatique" in modele_prefere else [modele_prefere]
    if "gemini-3.8-flash" not in modeles_a_tenter:
        modeles_a_tenter.append("gemini-3.8-flash")

    history_contents = []
    for msg in historique_ui:
        role = "user" if msg["role"] == "user" else "model"
        history_contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
        )

    derniere_erreur = None
    for cle in cles:
        client = genai.Client(api_key=cle)
        for m in modeles_a_tenter:
            for tentative in range(2):
                try:
                    chat = client.chats.create(
                        model=m,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            temperature=0.1
                        ),
                        history=history_contents
                    )
                    rep = chat.send_message(instruction_filtre)
                    if rep and rep.text and rep.text.strip():
                        return rep.text, m
                    else:
                        raise ValueError(f"Réponse silencieusement bloquée ou vide avec le modèle {m}.")
                except Exception as e:
                    derniere_erreur = e
                    msg_err = str(e)
                    if "503" in msg_err or "UNAVAILABLE" in msg_err:
                        if tentative == 0:
                            time.sleep(2)
                            continue
                    break
    raise derniere_erreur if derniere_erreur else Exception("Impossible de générer le contenu après épuisement des clés.")

# --- REMPLACEMENT TOTAL DU BANDEAU PAR LE LOGO ---
logo_b64 = obtenir_logo_base64("icone_detoure.png")

if logo_b64:
    st.markdown(f"""
    <div style="display: flex; justify-content: center; align-items: center; margin-bottom: 1.5rem; margin-top: 0.5rem;">
        <img src="data:image/png;base64,{logo_b64}" style="width: 250px; height: 250px; border-radius: 50%; object-fit: cover; box-shadow: 0 8px 20px rgba(0,0,0,0.15); border: 3px solid #093c39;" />
    </div>
    """, unsafe_allow_html=True)
else:
    st.warning("⚠️ Logo introuvable pour affichage.")

# ==========================================================
# VUE 1 : ASSISTANT OPÉRATOIRE
# ==========================================================
if st.session_state.menu_navigation == "💬 Assistant Opératoire":
    st.markdown("""
    <div class="app-workspace">
        <div class="filter-label">🎯 Sélectionner les sections à afficher :</div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        f_materiel = st.checkbox("⚡ Matériel (DMS/DMUU)", value=False)
    with col2:
        f_temps = st.checkbox("⏱️ Temps opératoires", value=False)
    with col3:
        f_mepc = st.checkbox("🛏️ MEPC (Position)", value=False)
    with col4:
        f_anatomie = st.checkbox("🫀 Anatomie", value=False)
    with col5:
        f_rappel = st.checkbox("📋 Rappel complet", value=False)

    if "chat_display" not in st.session_state:
        st.session_state.chat_display = []

    for idx, msg in enumerate(st.session_state.chat_display):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                col_u_txt, col_u_btn = st.columns([4, 1])
                with col_u_txt:
                    st.markdown(msg["content"])
                with col_u_btn:
                    st.markdown('<div class="btn-relancer">', unsafe_allow_html=True)
                    if st.button("🔄 Relancer", key=f"btn_user_relance_{idx}"):
                        st.session_state.derniere_requete = msg["content"]
                        st.session_state.relance_demandee = True
                        st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)
            else:
                schemas = msg.get("schemas", [])
                est_rappel_complet = msg.get("est_rappel_complet", False)
                afficher_contenu_complet(msg["content"], schemas, est_rappel_complet)

                pdf_bytes = creer_pdf(msg["content"])
                st.download_button(
                    label="📥 Télécharger résultat (PDF)",
                    data=pdf_bytes,
                    file_name=f"protocole_bloc_{idx}.pdf",
                    mime="application/pdf",
                    key=f"dl_msg_{idx}"
                )

    saisie_soignant = st.chat_input("Ex: Montre-moi une pince kocher, DPC, différence entre de bakey et resano ?")

    texte_a_traiter = None
    if saisie_soignant:
        texte_a_traiter = saisie_soignant
    elif st.session_state.relance_demandee and st.session_state.derniere_requete:
        texte_a_traiter = st.session_state.derniere_requete
        st.session_state.relance_demandee = False

    if texte_a_traiter:
        filtres_actifs = []
        est_rappel_complet = False

        req_norm_verif = normaliser_texte(texte_a_traiter)
        if f_rappel or "rappel complet" in req_norm_verif or "complet" in req_norm_verif:
            filtres_actifs.append("RAPPEL COMPLET")
            est_rappel_complet = True
        else:
            if f_materiel: filtres_actifs.append("MATÉRIEL")
            if f_temps: filtres_actifs.append("TEMPS OPÉRATOIRES")
            if f_mepc: filtres_actifs.append("MEPC (POSITION)")
            if f_anatomie: filtres_actifs.append("ANATOMIE")
            
            if not filtres_actifs:
                if not est_requete_purement_materiel(texte_a_traiter, []):
                    mots_generaux = ["ibode", "formation", "annee", "sleeve", "pourquoi", "comment", "qu'est", "montre", "différence"]
                    if not any(mg in req_norm_verif for mg in mots_generaux):
                        est_rappel_complet = True

        if filtres_actifs:
            instruction_filtre = f"[FILTRES: {', '.join(filtres_actifs)}] Demande pour : {texte_a_traiter}"
        else:
            instruction_filtre = texte_a_traiter

        if est_rappel_complet and (f_anatomie or f_rappel or "rappel complet" in req_norm_verif or "complet" in req_norm_verif or "temps" in req_norm_verif):
            st.session_state.retracter_sidebar = True

        st.session_state.derniere_requete = texte_a_traiter
        
        historique_a_envoyer = list(st.session_state.chat_display)

        st.session_state.chat_display.append({"role": "user", "content": texte_a_traiter})
        
        with st.chat_message("user"):
            col_u_txt, col_u_btn = st.columns([4, 1])
            with col_u_txt:
                st.markdown(texte_a_traiter)
            with col_u_btn:
                st.markdown('<div class="btn-relancer">', unsafe_allow_html=True)
                if st.button("🔄 Relancer", key=f"btn_user_relance_new_{len(st.session_state.chat_display)}"):
                    st.session_state.derniere_requete = texte_a_traiter
                    st.session_state.relance_demandee = True
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

        with st.chat_message("assistant"):
            container_attente = st.empty()
            gif_b64 = obtenir_gif_base64("loading.gif")
            
            if gif_b64:
                container_attente.markdown(f"""
                <div style="padding: 6px 0;">
                    <img src="data:image/gif;base64,{gif_b64}" width="300" style="border-radius: 8px;" />
                </div>
                """, unsafe_allow_html=True)
            else:
                container_attente.markdown("<p style='font-style: italic; font-size: 0.85rem; color: #52796f;'>Recherche en cours dans les protocoles du bloc...</p>", unsafe_allow_html=True)

            try:
                texte_reponse, modele_utilise = generer_avec_memoire_chat(
                    instruction_filtre, modele_choisi, cles_disponibles, historique_a_envoyer
                )
                
                images_liees = trouver_schemas_locaux(texte_a_traiter, filtres_actifs)
                container_attente.empty()

                afficher_contenu_complet(texte_reponse, images_liees, est_rappel_complet)

                pdf_bytes_new = creer_pdf(texte_reponse)
                st.download_button(
                    label="📥 Télécharger résultat (PDF)",
                    data=pdf_bytes_new,
                    file_name=f"protocole_bloc_{len(st.session_state.chat_display)}.pdf",
                    mime="application/pdf",
                    key=f"dl_new_{len(st.session_state.chat_display)}"
                )

                st.session_state.chat_display.append({
                    "role": "assistant",
                    "content": texte_reponse,
                    "schemas": images_liees,
                    "est_rappel_complet": est_rappel_complet
                })

                st.session_state.historique_requetes.insert(0, {
                    "question": texte_a_traiter,
                    "filtres": filtres_actifs if filtres_actifs else ["Recherche ciblée / Matériel"],
                    "reponse": texte_reponse,
                    "schemas": images_liees,
                    "est_rappel_complet": est_rappel_complet
                })

            except Exception as e:
                container_attente.empty()
                message_err = str(e)
                if "429" in message_err or "RESOURCE_EXHAUSTED" in message_err:
                    st.warning("⏳ **Quota d'appels temporairement saturé auprès de Google**. Patientez un instant avant de relancer votre recherche.")
                elif "503" in message_err or "UNAVAILABLE" in message_err:
                    st.warning("⏳ **Les serveurs d'intelligence artificielle de Google sont temporairement surchargés.** Veuillez patienter quelques instants et cliquer sur le bouton 'Relancer'.")
                else:
                    st.error(f"❌ Erreur de l'API Google : {message_err}")
                st.stop()
        
        st.rerun()

# ==========================================================
# VUE 2 : ONGLET HISTORIQUE DES REQUÊTES
# ==========================================================
elif st.session_state.menu_navigation == "📜 Historique des requêtes":
    st.markdown("### 📜 Historique des consultations de session")
    st.caption("Consultez et réutilisez les réponses générées au cours de votre vacation.")

    if st.session_state.historique_requetes:
        if st.button("🗑️ Vider l'historique"):
            st.session_state.historique_requetes = []
            st.session_state.chat_display = []
            st.session_state.derniere_requete = None
            st.rerun()

        st.write("")
        for idx, item in enumerate(st.session_state.historique_requetes):
            filtres_str = " • ".join(item["filtres"])
            images_liees = item.get("schemas", [])

            with st.expander(f"🔹 {item['question']}  [{filtres_str}]", expanded=(idx == 0)):
                afficher_contenu_complet(item["reponse"], images_liees, item.get("est_rappel_complet", False))

                pdf_bytes_hist = creer_pdf(item["reponse"])
                st.download_button(
                    label="📥 Télécharger résultat (PDF)",
                    data=pdf_bytes_hist,
                    file_name=f"historique_bloc_{idx}.pdf",
                    mime="application/pdf",
                    key=f"dl_hist_{idx}"
                )
    else:
        st.info("Aucune requête enregistrée dans cette session.")