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

def reinitialiser_memoire():
    st.session_state.texte_pret_pour_audio = None

if "texte_pret_pour_audio" not in st.session_state:
    st.session_state.texte_pret_pour_audio = None

def extraire_texte(fichier_telecharge) -> str:
    nom_fichier = fichier_telecharge.name.lower()
    texte_extrait = ""
    if nom_fichier.endswith(".txt"):
        texte_extrait = fichier_telecharge.read().decode("utf-8", errors="ignore")
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
    return re.sub(r'\s+', ' ', texte).strip()

# Découpage optimisé à 8000 caractères pour réduire drastiquement le nombre d'appels API
def decouper_texte_en_chunks(texte: str, taille_chunk: int = 8000) -> list:
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
    genai.configure(api_key=api_key.strip())
    model = genai.GenerativeModel('gemini-3.6-flash')
    prompt = f"Tu es un traducteur littéraire. Traduis le texte suivant de l'anglais vers un français fluide et naturel. Ne rajoute aucun commentaire.\n\nTexte :\n{chunk}"
    response = model.generate_content(prompt, generation_config={"temperature": 0.2})
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
# INTERFACE PRINCIPALE
# ==============================================================================
def main():
    st.title("🎛️ Le Studio Audio Master")
    st.divider()

    # Récupération automatique du pool de clés depuis secrets.toml
    cles_config = st.secrets.get("GOOGLE_API_KEYS", None)
    if cles_config is None:
        cle_unique = st.secrets.get("GOOGLE_API_KEY", "")
        pool_cles = [cle_unique] if cle_unique else []
    else:
        pool_cles = list(cles_config)

    with st.sidebar:
        st.header("1. Type de Document")
        mode_choisi = st.radio(
            "Langue d'origine du fichier :",
            ["🇫🇷 Document en Français", "🇬🇧 Document en Anglais"],
            on_change=reinitialiser_memoire
        )
        
        st.header("2. Configuration")
        if mode_choisi == "🇬🇧 Document en Anglais":
            st.caption(f"🔑 {len(pool_cles)} clé(s) API détectée(s) dans le pool.")
            cle_manuelle = st.text_input("Ajouter/Remplacer par une clé manuelle :", type="password")
            if cle_manuelle.strip():
                pool_cles = [cle_manuelle.strip()]

        choix_nom_voix = st.selectbox("Narrateur HD :", options=list(VOIX_FRANCAISES.keys()))
        voix_technique = VOIX_FRANCAISES[choix_nom_voix]

        if st.button("🔄 Réinitialiser l'application", use_container_width=True):
            reinitialiser_memoire()
            st.rerun()

    st.subheader(f"Étape 1 : Charger votre fichier ({mode_choisi.split(' ')[2]})")
    fichier_upload = st.file_uploader("Fichier .txt ou .pdf", type=["txt", "pdf"])

    if fichier_upload is not None:
        texte_brut = extraire_texte(fichier_upload)
        texte_propre = nettoyer_texte(texte_brut)
        
        if not texte_propre:
            st.error("❌ Le document semble vide ou illisible.")
            return

        st.success(f"✅ Extraction et nettoyage réussis ! ({len(texte_propre)} caractères)")

        # MODE ANGLAIS AVEC FAILOVER MULTI-CLÉS
        if mode_choisi == "🇬🇧 Document en Anglais":
            if st.session_state.texte_pret_pour_audio is None:
                st.subheader("Étape 2 : Traduction en Français")
                if st.button("🚀 Lancer la Traduction IA", type="primary"):
                    if not pool_cles or not pool_cles[0].strip():
                        st.error("🚨 Aucune clé API Google valide n'a été configurée.")
                        return

                    chunks_anglais = decouper_texte_en_chunks(texte_propre, taille_chunk=8000)
                    chunks_traduits = []
                    barre_progression = st.progress(0, text="Démarrage...")

                    index_cle = 0
                    i = 0
                    
                    while i < len(chunks_anglais):
                        pct = int(((i + 1) / len(chunks_anglais)) * 100)
                        barre_progression.progress(pct, text=f"Traduction partie {i + 1}/{len(chunks_anglais)} (Clé #{index_cle + 1})...")
                        
                        try:
                            traduction = traduire_chunk_gemini(chunks_anglais[i], pool_cles[index_cle])
                            chunks_traduits.append(traduction)
                            i += 1  # Passe au morceau suivant en cas de succès
                            time.sleep(1)
                        except Exception as e:
                            erreur_str = str(e).lower()
                            # Détection de dépassement de quota (429 / resource exhausted)
                            if "429" in erreur_str or "quota" in erreur_str or "resource_exhausted" in erreur_str:
                                if index_cle + 1 < len(pool_cles):
                                    index_cle += 1
                                    st.warning(f"⚠️ Quota atteint pour la clé #{index_cle}. Bascule automatique vers la clé #{index_cle + 1}...")
                                    time.sleep(2)
                                    # La boucle réessaie le même morceau i avec la nouvelle clé
                                else:
                                    # Sauvegarde des parties déjà traduites pour ne rien perdre
                                    if chunks_traduits:
                                        st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                                    st.error("❌ Toutes les clés API ont épuisé leur quota. Les morceaux déjà traduits ont été sauvegardés ci-dessous.")
                                    st.rerun()
                            else:
                                st.error(f"❌ Erreur inattendue : {str(e)}")
                                return

                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                    st.rerun()

        elif mode_choisi == "🇫🇷 Document en Français":
            st.session_state.texte_pret_pour_audio = texte_propre

        # ÉTAPE COMMUNE : AUDIO ET TÉLÉCHARGEMENT
        if st.session_state.texte_pret_pour_audio is not None:
            st.divider()
            st.subheader("Étape Finale : Votre texte Français est prêt ! 🎧")
            
            with st.expander("📖 Afficher le texte intégral", expanded=True):
                st.text_area("Texte Final", value=st.session_state.texte_pret_pour_audio, height=250)
            
            nom_base = fichier_upload.name.rsplit('.', 1)[0]
            st.download_button(
                label="📄 Télécharger le texte traduit (.txt)",
                data=st.session_state.texte_pret_pour_audio,
                file_name=f"Texte_FR_{nom_base}.txt",
                mime="text/plain",
                type="secondary"
            )

            st.write("---")
            if st.button("🎙️ Générer le Livre Audio HD", type="primary"):
                with st.spinner("🔊 Enregistrement studio en cours..."):
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
