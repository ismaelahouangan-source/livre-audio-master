import streamlit as st
import fitz  # PyMuPDF
import io
import asyncio
import tempfile
import os
import edge_tts
import re
import time
import google.generativeai as genai

# ==============================================================================
# CONFIGURATION DE LA PAGE & DES VOIX
# ==============================================================================
st.set_page_config(
    page_title="Le Studio Master - Traduction & Voix HD",
    page_icon="🎛️",
    layout="wide"
)

VOIX_FRANCAISES = {
    "Henri (Homme - Voix de narrateur grave)": "fr-FR-HenriNeural",
    "Denise (Femme - Douce & Naturelle)": "fr-FR-DeniseNeural",
    "Eloise (Femme - Dynamique & Claire)": "fr-FR-EloiseNeural",
    "Rémy (Homme - Clair & Standard)": "fr-FR-RemyNeural"
}

# ==============================================================================
# GESTION DE LA MÉMOIRE (SESSION STATE)
# ==============================================================================
def reinitialiser_memoire():
    st.session_state.texte_pret_pour_audio = None

if "texte_pret_pour_audio" not in st.session_state:
    st.session_state.texte_pret_pour_audio = None


# ==============================================================================
# FONCTIONS UTILITAIRES COMMUNES
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
            texte_extrait += page.get_text() + "\n"

    return texte_extrait.strip()

def nettoyer_texte(texte: str) -> str:
    texte = re.sub(r'(?<![.\!?])\n', ' ', texte)
    texte = re.sub(r'\s+([.,!?:;])', r'\1', texte)
    texte = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', texte)
    
    corrections = {
        "c h a p i t r e": "chapitre",
        "ber ger": "berger",
        "V oyant": "Voyant",
        "br ebis": "brebis",
        "dif ficile": "difficile"
    }
    for erreur, correction in corrections.items():
        texte = texte.replace(erreur, correction)
        texte = texte.replace(erreur.capitalize(), correction.capitalize())
        
    texte = re.sub(r'\s+', ' ', texte)
    return texte.strip()

def decouper_texte_en_chunks(texte: str, taille_chunk: int = 2000) -> list:
    if not texte:
        return []
    chunks = []
    paragraphes = texte.split(". ") 
    chunk_actuel = ""
    for paragraphe in paragraphes:
        if len(chunk_actuel) + len(paragraphe) > taille_chunk and len(chunk_actuel) > 0:
            chunks.append(chunk_actuel.strip())
            chunk_actuel = paragraphe + ". "
        else:
            chunk_actuel += paragraphe + ". "
    if chunk_actuel.strip():
        chunks.append(chunk_actuel.strip())
    return chunks

def traduire_chunk_gemini(chunk: str, api_key: str) -> str:
    # Configuration de l'API Google
    genai.configure(api_key=api_key)
    # Utilisation du modèle ultra-rapide et performant
    model = genai.GenerativeModel('gemini-3.6-flash')
    
    prompt = f"Tu es un traducteur littéraire. Traduis le texte suivant de l'anglais vers un français fluide et naturel. Ne rajoute aucun commentaire.\n\nTexte :\n{chunk}"
    
    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0.2}
    )
    return response.text.strip()

async def generer_audio_edge_async(texte: str, voix: str, chemin_sortie: str):
    communicate = edge_tts.Communicate(texte, voix)
    await communicate.save(chemin_sortie)

def generer_audio_hd(texte_francais: str, voix_choisie: str) -> bytes:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fichier_temp:
        chemin_temp = fichier_temp.name
    asyncio.run(generer_audio_edge_async(texte_francais, voix_choisie, chemin_temp))
    with open(chemin_temp, "rb") as f:
        donnees_audio = f.read()
    os.remove(chemin_temp)
    return donnees_audio


# ==============================================================================
# INTERFACE PRINCIPALE (LE COUTEAU SUISSE)
# ==============================================================================
def main():
    st.title("🎛️ Le Studio Audio Master")
    st.markdown("Votre pipeline complet : Extraction PyMuPDF ➡️ Traduction Gemini 3.6 Flash ➡️ Synthèse Vocale Edge-TTS.")
    st.divider()

    with st.sidebar:
        st.header("1. Type de Document")
        mode_choisi = st.radio(
            "Langue d'origine du fichier :",
            ["🇫🇷 Document en Français", "🇬🇧 Document en Anglais"],
            on_change=reinitialiser_memoire
        )
        
        st.header("2. Configuration")
        api_key_google = ""
        
        if mode_choisi == "🇬🇧 Document en Anglais":
            api_key_google = st.text_input("Clé d'API Google (Requise pour traduire)", type="password")
            
        choix_nom_voix = st.selectbox("Narrateur HD :", options=list(VOIX_FRANCAISES.keys()))
        voix_technique = VOIX_FRANCAISES[choix_nom_voix]

        if st.button("🔄 Réinitialiser l'application", use_container_width=True):
            reinitialiser_memoire()
            st.rerun()

    # --- ZONE PRINCIPALE ---
    st.subheader(f"Étape 1 : Charger votre fichier ({mode_choisi.split(' ')[2]})")
    fichier_upload = st.file_uploader("Fichier .txt ou .pdf", type=["txt", "pdf"])

    if fichier_upload is not None:
        
        texte_brut = extraire_texte(fichier_upload)
        texte_propre = nettoyer_texte(texte_brut)
        
        if not texte_propre:
            st.error("❌ Le document semble vide ou illisible.")
            return

        st.success(f"✅ Extraction et nettoyage réussis ! ({len(texte_propre)} caractères)")
        
        with st.expander("📄 Aperçu du texte extrait du fichier", expanded=False):
            st.text_area("Texte original", value=texte_propre[:2000] + "...", height=150, disabled=True)

        # ======================================================================
        # BRANCHE A : MODE ANGLAIS
        # ======================================================================
        if mode_choisi == "🇬🇧 Document en Anglais":
            
            if st.session_state.texte_pret_pour_audio is None:
                st.subheader("Étape 2 : Traduction en Français")
                if st.button("🚀 Lancer la Traduction IA", type="primary"):
                    if not api_key_google.strip():
                        st.error("🚨 Veuillez d'abord renseigner votre clé d'API Google dans le menu de gauche.")
                        return

                    chunks_anglais = decouper_texte_en_chunks(texte_propre, taille_chunk=2000)
                    chunks_traduits = []
                    barre_progression = st.progress(0, text="Initialisation de Gemini...")

                    for index, chunk in enumerate(chunks_anglais):
                        pct = int(((index + 1) / len(chunks_anglais)) * 100)
                        barre_progression.progress(pct, text=f"Traduction partie {index + 1}/{len(chunks_anglais)}...")
                        try:
                            traduction = traduire_chunk_gemini(chunk, api_key_google)
                            chunks_traduits.append(traduction)
                            time.sleep(2)
                        except Exception as e:
                            st.error(f"❌ Erreur de traduction : {str(e)}")
                            return

                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                    st.rerun()

        # ======================================================================
        # BRANCHE B : MODE FRANÇAIS
        # ======================================================================
        elif mode_choisi == "🇫🇷 Document en Français":
            st.session_state.texte_pret_pour_audio = texte_propre


        # ======================================================================
        # ÉTAPE FINALE COMMUNE : LE STUDIO AUDIO
        # ======================================================================
        if st.session_state.texte_pret_pour_audio is not None:
            
            st.divider()
            st.subheader("Étape Finale : Votre texte Français est prêt ! 🎧")
            
            with st.expander("📖 Afficher le texte intégral (prêt à être lu)", expanded=True):
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
            
            if st.button("🎙️ Générer le Livre Audio HD", type="primary"):
                with st.spinner("🔊 Enregistrement studio en cours... L'IA s'échauffe la voix."):
                    try:
                        donnees_audio_mp3 = generer_audio_hd(st.session_state.texte_pret_pour_audio, voix_technique)
                        
                        st.success("🎉 Livre Audio HD généré avec succès !")
                        st.audio(donnees_audio_mp3, format="audio/mp3")
                        st.download_button(
                            label="⬇️ Télécharger le MP3 HD",
                            data=donnees_audio_mp3,
                            file_name=f"Audio_HD_{nom_base}.mp3",
                            mime="audio/mp3",
                            type="primary"
                        )
                    except Exception as e:
                        st.error(f"❌ Erreur lors de la création audio : {str(e)}")

if __name__ == "__main__":
    main()