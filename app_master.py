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

def assainir_cle(cle_brute: str) -> str:
    """Élimine les résidus de copier-coller Markdown, quotes et espaces."""
    return (
        cle_brute.replace(r'\_', '_')
        .replace('\\', '')
        .strip()
        .strip('"')
        .strip("'")
    )

def traduire_chunk_gemini(chunk: str, api_key: str) -> str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3.6-flash')
    
    prompt = (
        "Tu es un traducteur littéraire professionnel. "
        "Traduis le texte suivant de l'anglais vers un français fluide, élégant et naturel. "
        "Ne rajoute aucune note, avertissement ou commentaire.\n\n"
        f"Texte :\n{chunk}"
    )
    
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
# INTERFACE PRINCIPALE
# ==============================================================================
def main():
    st.title("🎛️ Le Studio Audio Master")
    st.markdown("Pipeline haute performance : PyMuPDF ➡️ Gemini 3.6 Flash (Pool Multi-clés) ➡️ Edge-TTS HD.")
    st.divider()

    # Détection et préparation du pool de clés
    cles_brutes = st.secrets.get("GOOGLE_API_KEYS", None)
    if cles_brutes is None:
        cle_solo = st.secrets.get("GOOGLE_API_KEY", "")
        pool_initial = [cle_solo] if cle_solo else []
    else:
        pool_initial = list(cles_brutes)

    # Nettoyage préventif global
    pool_cles = [assainir_cle(k) for k in pool_initial if assainir_cle(k)]

    with st.sidebar:
        st.header("1. Type de Document")
        mode_choisi = st.radio(
            "Langue d'origine du fichier :",
            ["🇫🇷 Document en Français", "🇬🇧 Document en Anglais"],
            on_change=reinitialiser_memoire
        )
        
        st.header("2. Configuration")
        if mode_choisi == "🇬🇧 Document en Anglais":
            st.caption(f"🔑 **{len(pool_cles)} clé(s) active(s)** dans le pool.")
            cle_manuelle = st.text_input("Remplacer temporairement par une clé :", type="password")
            if cle_manuelle.strip():
                pool_cles = [assainir_cle(cle_manuelle)]

        choix_nom_voix = st.selectbox("Narrateur HD :", options=list(VOIX_FRANCAISES.keys()))
        voix_technique = VOIX_FRANCAISES[choix_nom_voix]

        if st.button("🔄 Réinitialiser l'application", use_container_width=True):
            reinitialiser_memoire()
            st.rerun()

    # --- CORPS PRINCIPAL ---
    st.subheader(f"Étape 1 : Charger votre fichier ({mode_choisi.split(' ')[2]})")
    fichier_upload = st.file_uploader("Fichier .txt ou .pdf", type=["txt", "pdf"])

    if fichier_upload is not None:
        texte_brut = extraire_texte(fichier_upload)
        texte_propre = nettoyer_texte(texte_brut)
        
        if not texte_propre:
            st.error("❌ Le document semble vide ou illisible.")
            return

        st.success(f"✅ Extraction et nettoyage réussis ! ({len(texte_propre)} caractères détectés)")
        
        with st.expander("📄 Aperçu du texte extrait du fichier", expanded=False):
            st.text_area("Texte extrait", value=texte_propre[:2000] + "...", height=150, disabled=True)

        # ======================================================================
        # BRANCHE A : MODE ANGLAIS AVEC FAILOVER IMMUNISÉ
        # ======================================================================
        if mode_choisi == "🇬🇧 Document en Anglais":
            if st.session_state.texte_pret_pour_audio is None:
                st.subheader("Étape 2 : Traduction en Français")
                
                if st.button("🚀 Lancer la Traduction IA", type="primary"):
                    if not pool_cles:
                        st.error("🚨 Aucune clé API Google valide trouvée dans les Secrets ou la saisie manuelle.")
                        return

                    chunks_anglais = decouper_texte_en_chunks(texte_propre, taille_chunk=8000)
                    chunks_traduits = []
                    barre_progression = st.progress(0, text="Initialisation...")

                    index_cle = 0
                    i = 0

                    while i < len(chunks_anglais):
                        pct = int(((i + 1) / len(chunks_anglais)) * 100)
                        barre_progression.progress(
                            pct, 
                            text=f"Traduction partie {i + 1}/{len(chunks_anglais)} (via Clé #{index_cle + 1}/{len(pool_cles)})..."
                        )
                        
                        cle_active = pool_cles[index_cle]

                        try:
                            traduction = traduire_chunk_gemini(chunks_anglais[i], cle_active)
                            chunks_traduits.append(traduction)
                            i += 1  # Morceau validé, on avance
                            time.sleep(1)

                        except Exception as e:
                            erreur_str = str(e).lower()
                            
                            # Diagnostic précis de la cause
                            if "429" in erreur_str or "quota" in erreur_str or "resource_exhausted" in erreur_str:
                                diagnostic = "Quota par minute ou plafond journalier atteint (Erreur 429)"
                            elif "401" in erreur_str or "invalid authentication" in erreur_str or "access_token_type_unsupported" in erreur_str:
                                diagnostic = "Clé invalide, révoquée ou format corrompu (Erreur 401)"
                            elif "403" in erreur_str or "permission_denied" in erreur_str:
                                diagnostic = "Permission refusée ou restrictions de l'API (Erreur 403)"
                            else:
                                diagnostic = f"Erreur de service ({str(e)[:120]})"

                            # Bascule sur la clé suivante
                            if index_cle + 1 < len(pool_cles):
                                st.warning(
                                    f"⚠️ **Clé #{index_cle + 1} écartée** : {diagnostic}. "
                                    f"Bascule immédiate sur la **Clé #{index_cle + 2}**..."
                                )
                                index_cle += 1
                                time.sleep(1.5)
                            else:
                                if chunks_traduits:
                                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                                st.error(
                                    f"🚨 **Échec définitif sur la Clé #{index_cle + 1}** : {diagnostic}. "
                                    "Toutes les clés enregistrées ont été consommées ou rejetées."
                                )
                                st.rerun()

                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                    st.rerun()

        # ======================================================================
        # BRANCHE B : MODE FRANÇAIS
        # ======================================================================
        elif mode_choisi == "🇫🇷 Document en Français":
            st.session_state.texte_pret_pour_audio = texte_propre

        # ======================================================================
        # ÉTAPE COMMUNE : AUDIO ET TÉLÉCHARGEMENT
        # ======================================================================
        if st.session_state.texte_pret_pour_audio is not None:
            st.divider()
            st.subheader("Étape Finale : Votre texte Français est prêt ! 🎧")
            
            with st.expander("📖 Afficher le texte intégral (prêt à être lu)", expanded=True):
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
