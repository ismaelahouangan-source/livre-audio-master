import streamlit as st
import fitz  # PyMuPDF
import io
import asyncio
import tempfile
import os
import edge_tts
import re
import time
import requests
import base64
import google.generativeai as genai
from pydub import AudioSegment

# ==============================================================================
# CONFIGURATION DE LA PAGE & DES VOIX GEMINI TTS
# ==============================================================================
st.set_page_config(
    page_title="Studio Master - Gemini TTS Premium",
    page_icon="🎙️",
    layout="wide"
)

VOIX_GEMINI_TTS = {
    "Bodi (Homme - Calme, intimiste, voix grave)": "Bodi",
    "Lumi (Femme - Chaleureuse, voix grave)": "Lumi",
    "Sadaltager (Homme - Érudit, voix médium)": "Sadaltager",
    "Koda (Homme - Direct et utile, voix médium-grave)": "Koda"
}

def reinitialiser_memoire():
    st.session_state.texte_pret_pour_audio = None

if "texte_pret_pour_audio" not in st.session_state:
    st.session_state.texte_pret_pour_audio = None

# ==============================================================================
# FONCTIONS DE STRUCTURE ET DE NETTOYAGE
# ==============================================================================
def extraire_texte(fichier_telecharge) -> str:
    nom_fichier = fichier_telecharge.name.lower()
    texte_extrait = ""

    if nom_fichier.endswith(".txt"):
        bytes_data = fichier_telecharge.read()
        texte_extrait = bytes_data.decode("utf-8", errors="ignore")
    elif nom_fichier.endswith(".pdf"):
        doc = fitz.open(stream=fichier_telecharge.read(), filetype="pdf")
        for page in doc:
            texte_extrait += page.get_text() + "\n\n"

    return texte_extrait.strip()

def filtrer_notes_et_artefacts(texte: str) -> str:
    if not texte:
        return ""
    lignes = texte.split("\n")
    lignes_filtrees = []
    index_coupure = len(lignes)
    pattern_section_notes = re.compile(r'^\s*(notes?\s+de\s+bas\s+de\s+page|footnotes?|notes?)\s*$', re.IGNORECASE)

    for idx, ligne in enumerate(lignes):
        ligne_strip = ligne.strip()
        if pattern_section_notes.match(ligne_strip):
            index_coupure = idx
            break
        if idx > len(lignes) * 0.6 and re.match(r'^[1I]\s*[\.\-\)]\s+[A-ZÀ-ÖØ-ß]', ligne_strip):
            texte_suivant = "\n".join(lignes[idx:idx+15])
            if re.search(r'\n[2]\s*[\.\-\)]', texte_suivant):
                index_coupure = idx
                break

    lignes_filtrees = lignes[:index_coupure]
    texte_assaini = "\n".join(lignes_filtrees)
    texte_assaini = re.sub(r'(?<=[a-zA-ZÀ-ÿ\.\,\!\?\)])(\d{1,2})(?=[^\d%\w]|$)(?!\s*%)', '', texte_assaini)
    texte_assaini = re.sub(r'^\s*I\s*\n+', '', texte_assaini)
    return texte_assaini

def nettoyer_texte_source(texte: str) -> str:
    texte = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', texte)
    texte = re.sub(r'([a-zA-ZÀ-ÿ,\'’])\n\s*\n\s*([a-zà-öø-ÿ])', r'\1 \2', texte)
    texte = re.sub(r'(?<!\n)\n(?!\n)', ' ', texte)
    texte = re.sub(r'(CHAPITRE\s+\d+)\s+([^\n.]+)', r'\1\n\n\2\n\n', texte, flags=re.IGNORECASE)
    texte = re.sub(r'([.?!])\s+([A-ZÀ-ÖØ-ß\s\':-]{4,45})\s+([A-ZÀ-ÖØ-ß][a-zà-öø-ÿ])', r'\1\n\n\2\n\n\3', texte)

    corrections = {"c h a p i t r e": "chapitre", "ber ger": "berger", "V oyant": "Voyant", "br ebis": "brebis", "dif ficile": "difficile"}
    for erreur, correction in corrections.items():
        texte = texte.replace(erreur, correction)
        texte = texte.replace(erreur.capitalize(), correction.capitalize())

    texte = re.sub(r'[ \t]+', ' ', texte)
    texte = re.sub(r'\n{3,}', '\n\n', texte)
    return texte.strip()

def nettoyer_texte_pour_audio(texte: str) -> str:
    if not texte:
        return ""
    texte = re.sub(r'(\d+):(\d+)', r'\1, \2', texte)
    texte = re.sub(r'\b([cdjlnmstCDJLNMS]|qu|QU|Qu)\s+([aeiouyhéèêàâîïôûùAEIOUYHÉÈÊÀÂÎÏÔÛÙ])', r"\1'\2", texte)
    texte = texte.replace("*", "")
    texte = re.sub(r'^#+\s*', '', texte, flags=re.MULTILINE)
    texte = re.sub(r'(?<=\s)_(?=\S)|(?<=\S)_(?=\s)', '', texte)
    texte = texte.replace("_", "")
    texte = re.sub(r'\s*[—–]\s*', ', ', texte)
    texte = re.sub(r'^\s*[—–]\s*', '', texte, flags=re.MULTILINE)
    texte = texte.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')
    texte = re.sub(r'[ \t]+', ' ', texte)
    texte = re.sub(r' +(?=\n)', '', texte)
    texte = re.sub(r'\n\s*\n', '\n\n', texte)
    texte = re.sub(r'\n{3,}', '\n\n', texte)
    return texte.strip()

def decouper_texte_en_chunks(texte: str, taille_chunk: int = 75000) -> list:
    if not texte:
        return []
    paragraphes = texte.split("\n\n")
    chunks = []
    chunk_actuel = ""
    for paragraphe in paragraphes:
        paragraphe_propre = paragraphe.strip()
        if not paragraphe_propre:
            continue
        if len(chunk_actuel) + len(paragraphe_propre) + 2 > taille_chunk and len(chunk_actuel) > 0:
            chunks.append(chunk_actuel.strip())
            chunk_actuel = paragraphe_propre + "\n\n"
        else:
            chunk_actuel += paragraphe_propre + "\n\n"
    if chunk_actuel.strip():
        chunks.append(chunk_actuel.strip())
    return chunks

def assainir_cle(cle_brute: str) -> str:
    return cle_brute.replace(r'\_', '_').replace('\\', '').strip().strip('"').strip("'")

# ==============================================================================
# MOTEUR DE TRADUCTION IA (Gemini 3.8 Flash)
# ==============================================================================
SYSTEM_INSTRUCTION = (
    "Tu es un traducteur littéraire et éditeur de premier ordre. "
    "Traduis le texte anglais fourni vers un français fluide, naturel et élégant.\n\n"
    "RÈGLES ABSOLUES D'AÉRATION ET DE MISE EN PAGE :\n"
    "- Préserve impérativement une structure TRÈS AÉRÉE. Ne produis JAMAIS de blocs compacts ou de pavés denses.\n"
    "- Le titre du chapitre et son sous-titre doivent obligatoirement être isolés sur leurs propres lignes avec un double saut de ligne (\\n\\n).\n"
    "- Chaque changement de sujet, dialogue ou citation doit constituer un paragraphe distinct séparé par un double saut de ligne (\\n\\n).\n"
    "- Démarre IMMÉDIATEMENT la traduction. Ne produis aucun commentaire ni étape de réflexion.\n"
    "- N'utilise AUCUN balisage Markdown : AUCUN astérisque (* ou **), AUCUN dièse (#), AUCUN souligné (_)."
)

def traduire_chunk_gemini(chunk: str, api_key: str) -> str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3.8-flash', system_instruction=SYSTEM_INSTRUCTION)
    try:
        generation_config = genai.types.GenerationConfig(temperature=0.2, max_output_tokens=65536, thinking_config={"thinking_budget": 0})
        response = model.generate_content(chunk, generation_config=generation_config)
    except Exception:
        generation_config = genai.types.GenerationConfig(temperature=0.2, max_output_tokens=65536)
        response = model.generate_content(chunk, generation_config=generation_config)
    return response.text.strip()

# ==============================================================================
# NOUVEAU MOTEUR AUDIO : GEMINI 3.8 FLASH TTS + FUSION PYDUB
# ==============================================================================
def generer_audio_gemini_tts(texte_francais: str, api_key: str, nom_voix: str) -> bytes:
    """
    Génère l'audio par blocs, fusionne proprement les formats bruts via Pydub,
    respecte la limite de 10 requêtes par minute (RPM) et compresse le tout en un MP3 léger.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    paragraphes = texte_francais.split("\n\n")
    piste_master = AudioSegment.empty()
    bloc_texte = ""

    # Limite sécuritaire d'environ 1500 caractères par appel TTS
    for i, para in enumerate(paragraphes):
        if len(bloc_texte) + len(para) > 1500:
            donnees_brutes = _requete_api_tts(bloc_texte, nom_voix, url, headers)
            # Chargement propre du flux audio en mémoire via Pydub
            segment = AudioSegment.from_file(io.BytesIO(donnees_brutes))
            piste_master += segment
            
            bloc_texte = para + "\n\n"
            # Pause de 6.5 secondes pour garantir un maximum de 9 requêtes par minute (limite stricte = 10)
            time.sleep(6.5) 
        else:
            bloc_texte += para + "\n\n"
            
    # Traitement du dernier bloc
    if bloc_texte.strip():
        donnees_brutes = _requete_api_tts(bloc_texte, nom_voix, url, headers)
        segment = AudioSegment.from_file(io.BytesIO(donnees_brutes))
        piste_master += segment

    # Export final avec compression MP3 (réduit drastiquement le poids du fichier final)
    buffer_sortie = io.BytesIO()
    piste_master.export(buffer_sortie, format="mp3", bitrate="128k")
    return buffer_sortie.getvalue()

def _requete_api_tts(texte: str, nom_voix: str, url: str, headers: dict) -> bytes:
    payload = {
        "contents": [{"parts": [{"text": texte}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": nom_voix
                    }
                }
            }
        }
    }
    
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        resultat = response.json()
        try:
            audio_b64 = resultat['candidates'][0]['content']['parts'][0]['inlineData']['data']
            return base64.b64decode(audio_b64)
        except KeyError:
            raise Exception("Format de réponse audio inattendu de l'API Gemini TTS.")
    else:
        raise Exception(f"Erreur API TTS (HTTP {response.status_code}): {response.text}")

# ==============================================================================
# INTERFACE PRINCIPALE
# ==============================================================================
def main():
    st.title("🎙️ Le Studio Master - Gemini TTS Premium")
    st.markdown("Pipeline Ultra-Réaliste : PyMuPDF ➡️ **Gemini 3.8 Flash** ➡️ **Gemini 3.8 Flash TTS**.")
    st.divider()

    cles_brutes = st.secrets.get("GOOGLE_API_KEYS", None)
    if cles_brutes is None:
        cle_solo = st.secrets.get("GOOGLE_API_KEY", "")
        pool_initial = [cle_solo] if cle_solo else []
    else:
        pool_initial = list(cles_brutes)

    pool_cles = [assainir_cle(k) for k in pool_initial if assainir_cle(k)]

    with st.sidebar:
        st.header("1. Type de Document")
        mode_choisi = st.radio(
            "Langue d'origine :",
            ["🇫🇷 Document en Français", "🇬🇧 Document en Anglais"],
            on_change=reinitialiser_memoire
        )

        st.header("2. Configuration")
        if mode_choisi == "🇬🇧 Document en Anglais":
            st.caption(f"🔑 **{len(pool_cles)} clé(s) active(s)** dans le pool Pro.")
            cle_manuelle = st.text_input("Remplacer temporairement par une clé :", type="password")
            if cle_manuelle.strip():
                pool_cles = [assainir_cle(cle_manuelle)]

        choix_nom_voix = st.selectbox("Narrateur Premium Gemini :", options=list(VOIX_GEMINI_TTS.keys()))
        voix_technique = VOIX_GEMINI_TTS[choix_nom_voix]

        if st.button("🔄 Réinitialiser", use_container_width=True):
            reinitialiser_memoire()
            st.rerun()

    st.subheader(f"Étape 1 : Charger votre fichier ({mode_choisi.split(' ')[2]})")
    fichier_upload = st.file_uploader("Fichier .txt ou .pdf", type=["txt", "pdf"])

    if fichier_upload is not None:
        texte_brut = extraire_texte(fichier_upload)
        texte_sans_notes = filtrer_notes_et_artefacts(texte_brut)
        texte_propre = nettoyer_texte_source(texte_sans_notes)

        if not texte_propre:
            st.error("❌ Le document semble vide ou illisible.")
            return

        st.success(f"✅ Extraction réussie ! ({len(texte_propre)} caractères détectés)")

        with st.expander("📄 Aperçu du texte", expanded=False):
            st.text_area("Texte source", value=texte_propre[:2000] + "...", height=150, disabled=True)

        if mode_choisi == "🇬🇧 Document en Anglais":
            if st.session_state.texte_pret_pour_audio is None:
                st.subheader("Étape 2 : Traduction en Français")

                if st.button("🚀 Lancer la Traduction Pro IA", type="primary"):
                    if not pool_cles:
                        st.error("🚨 Aucune clé API Google valide trouvée.")
                        return

                    chunks_anglais = decouper_texte_en_chunks(texte_propre, taille_chunk=75000)
                    chunks_traduits = []
                    barre_progression = st.progress(0, text="Initialisation de Gemini 3.8 Flash...")

                    index_cle = 0
                    i = 0

                    while i < len(chunks_anglais):
                        pct = int(((i + 1) / len(chunks_anglais)) * 100)
                        barre_progression.progress(pct, text=f"Traduction du bloc {i + 1}/{len(chunks_anglais)}...")
                        cle_active = pool_cles[index_cle]

                        try:
                            traduction_brute = traduire_chunk_gemini(chunks_anglais[i], cle_active)
                            traduction_propre = nettoyer_texte_pour_audio(traduction_brute)
                            chunks_traduits.append(traduction_propre)
                            i += 1
                            time.sleep(1)

                        except Exception as e:
                            erreur_str = str(e).lower()
                            raw_error = str(e)
                            diagnostic = f"Erreur API ({str(e)[:120]})"
                            if "429" in erreur_str: diagnostic = "Plafond temporaire ou limite atteinte (429)"
                            elif "401" in erreur_str: diagnostic = "Clé invalide ou révoquée (401)"
                            elif "403" in erreur_str: diagnostic = "Projet sans facturation active ou accès refusé (403)"

                            if index_cle + 1 < len(pool_cles):
                                st.warning(f"⚠️ Bascule immédiate sur la Clé #{index_cle + 2}...\n\n```text\n{raw_error}\n```")
                                index_cle += 1
                                time.sleep(1.5)
                            else:
                                st.error(f"🚨 Échec définitif.\n\n```text\n{raw_error}\n```")
                                st.rerun()

                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                    st.rerun()

        elif mode_choisi == "🇫🇷 Document en Français":
            st.session_state.texte_pret_pour_audio = nettoyer_texte_pour_audio(texte_propre)

        if st.session_state.texte_pret_pour_audio is not None:
            st.divider()
            st.subheader("Étape Finale : Votre texte Français est prêt ! 🎧")

            with st.expander("📖 Afficher le texte intégral structuré", expanded=True):
                st.text_area("Texte Final", value=st.session_state.texte_pret_pour_audio, height=250)

            nom_base = fichier_upload.name.rsplit('.', 1)[0]
            st.download_button(
                label="📄 Télécharger le texte (.txt)",
                data=st.session_state.texte_pret_pour_audio,
                file_name=f"Texte_FR_{nom_base}.txt",
                mime="text/plain",
                type="secondary"
            )

            st.write("---")
            if st.button("🎙️ Générer le Livre Audio Premium (Voix Bodi)", type="primary"):
                if not pool_cles:
                    st.error("🚨 Clé API requise pour la génération vocale Gemini.")
                    return
                    
                with st.spinner("🔊 Enregistrement studio par Gemini TTS en cours (laissez faire la magie)..."):
                    try:
                        texte_final_audio = nettoyer_texte_pour_audio(st.session_state.texte_pret_pour_audio)
                        cle_tts = pool_cles[0] 
                        donnees_audio_mp3 = generer_audio_gemini_tts(texte_final_audio, cle_tts, voix_technique)

                        st.success("🎉 Livre Audio Premium généré avec succès !")
                        st.audio(donnees_audio_mp3, format="audio/mp3")
                        st.download_button(
                            label="⬇️️ Télécharger le MP3 Compressé",
                            data=donnees_audio_mp3,
                            file_name=f"Audio_Premium_{nom_base}.mp3",
                            mime="audio/mp3",
                            type="primary"
                        )
                    except Exception as e:
                        st.error(f"❌ Erreur lors de la création audio via Gemini TTS : {str(e)}")

if __name__ == "__main__":
    main()
