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
    """Coupe le bloc de notes final et supprime les appels de notes sans toucher aux pourcentages."""
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

    # Suppression stricte des appels de notes collés au texte
    texte_assaini = re.sub(r'(?<=[a-zA-ZÀ-ÿ\.\,\!\?\)])(\d{1,2})(?=[^\d%\w]|$)(?!\s*%)', '', texte_assaini)
    texte_assaini = re.sub(r'^\s*I\s*\n+', '', texte_assaini)

    return texte_assaini

def nettoyer_texte_source(texte: str) -> str:
    """Répare les césures, recoud les phrases brisées par les sauts de page et aère les titres."""
    texte = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', texte)
    texte = re.sub(r'([a-zA-ZÀ-ÿ,\'’])\n\s*\n\s*([a-zà-öø-ÿ])', r'\1 \2', texte)
    texte = re.sub(r'(?<!\n)\n(?!\n)', ' ', texte)

    texte = re.sub(r'(CHAPITRE\s+\d+)\s+([^\n.]+)', r'\1\n\n\2\n\n', texte, flags=re.IGNORECASE)
    texte = re.sub(
        r'([.?!])\s+([A-ZÀ-ÖØ-ß\s\':-]{4,45})\s+([A-ZÀ-ÖØ-ß][a-zà-öø-ÿ])',
        r'\1\n\n\2\n\n\3',
        texte
    )

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

    texte = re.sub(r'[ \t]+', ' ', texte)
    texte = re.sub(r'\n{3,}', '\n\n', texte)
    return texte.strip()

def nettoyer_texte_pour_audio(texte: str) -> str:
    """Formate le texte traduit pour la fluidité et l'exactitude de la lecture audio par Edge-TTS."""
    if not texte:
        return ""

    # Correction phonétique des références bibliques (Job 38:4 -> Job 38, 4)
    texte = re.sub(r'(\d+):(\d+)', r'\1, \2', texte)

    # Répare les élisions orphelines (ex: "s installer" -> "s'installer")
    texte = re.sub(r'\b([cdjlnmstCDJLNMS]|qu|QU|Qu)\s+([aeiouyhéèêàâîïôûùAEIOUYHÉÈÊÀÂÎÏÔÛÙ])', r"\1'\2", texte)

    # Suppression du Markdown
    texte = texte.replace("*", "")
    texte = re.sub(r'^#+\s*', '', texte, flags=re.MULTILINE)
    texte = re.sub(r'(?<=\s)_(?=\S)|(?<=\S)_(?=\s)', '', texte)
    texte = texte.replace("_", "")

    # Ponctuation fluide et tirets
    texte = re.sub(r'\s*[—–]\s*', ', ', texte)
    texte = re.sub(r'^\s*[—–]\s*', '', texte, flags=re.MULTILINE)
    texte = texte.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')

    # Aération stricte des paragraphes
    texte = re.sub(r'[ \t]+', ' ', texte)
    texte = re.sub(r' +(?=\n)', '', texte)
    texte = re.sub(r'\n\s*\n', '\n\n', texte)
    texte = re.sub(r'\n{3,}', '\n\n', texte)

    return texte.strip()

def decouper_texte_en_chunks(texte: str, taille_chunk: int = 75000) -> list:
    """Permet de traiter jusqu'à 75 000 caractères par bloc pour traduire un chapitre d'un seul tenant."""
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
# MOTEUR DE TRADUCTION IA (STRUCTURE ET ÉCO-TOKENS)
# ==============================================================================
SYSTEM_INSTRUCTION = (
    "Tu es un traducteur littéraire et éditeur de premier ordre. "
    "Traduis le texte anglais fourni vers un français fluide, naturel et élégant.\n\n"
    "RÈGLES ABSOLUES D'AÉRATION ET DE MISE EN PAGE :\n"
    "- Préserve impérativement une structure TRÈS AÉRÉE. Ne produis JAMAIS de blocs compacts ou de pavés denses.\n"
    "- Le titre du chapitre et son sous-titre doivent obligatoirement être isolés sur leurs propres lignes avec un double saut de ligne (\\n\\n).\n"
    "- Chaque changement de sujet, chaque dialogue et chaque citation doit constituer un paragraphe distinct séparé par un double saut de ligne (\\n\\n).\n"
    "- Démarre IMMÉDIATEMENT la traduction. Ne produis aucun commentaire, aucune analyse, aucune étape de réflexion.\n"
    "- N'utilise AUCUN balisage Markdown : AUCUN astérisque (* ou **), AUCUN dièse (#), AUCUN souligné (_)."
)

def traduire_chunk_gemini(chunk: str, api_key: str) -> str:
    genai.configure(api_key=api_key)
    
    model = genai.GenerativeModel(
        model_name='gemini-3.8-flash',
        system_instruction=SYSTEM_INSTRUCTION
    )

    # Paramétrage strict pour éviter la facturation des tokens de réflexion inutiles
    try:
        generation_config = genai.types.GenerationConfig(
            temperature=0.2,
            max_output_tokens=65536,
            thinking_config={"thinking_budget": 0}
        )
        response = model.generate_content(chunk, generation_config=generation_config)
    except Exception:
        # Fallback si l'argument thinking_config est rejeté par la version du SDK
        generation_config = genai.types.GenerationConfig(
            temperature=0.2,
            max_output_tokens=65536
        )
        response = model.generate_content(chunk, generation_config=generation_config)

    return response.text.strip()

# ==============================================================================
# MOTEUR AUDIO EDGE-TTS
# ==============================================================================
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
    st.markdown("Pipeline Haute Fidélité : PyMuPDF ➡️️ **Gemini 3.8 Flash (Éco-Tokens)** ➡️ **Edge-TTS HD**.")
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
            "Langue d'origine du fichier :",
            ["🇫🇷 Document en Français", "🇬🇧 Document en Anglais"],
            on_change=reinitialiser_memoire
        )

        st.header("2. Configuration")
        if mode_choisi == "🇬🇧 Document en Anglais":
            st.caption(f"🔑 **{len(pool_cles)} clé(s) active(s)** dans le pool Pro.")
            cle_manuelle = st.text_input("Remplacer temporairement par une clé :", type="password")
            if cle_manuelle.strip():
                pool_cles = [assainir_cle(cle_manuelle)]

        choix_nom_voix = st.selectbox("Narrateur HD :", options=list(VOIX_FRANCAISES.keys()))
        voix_technique = VOIX_FRANCAISES[choix_nom_voix]

        if st.button("🔄 Réinitialiser l'application", use_container_width=True):
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

        with st.expander("📄 Aperçu du texte extrait structuré", expanded=False):
            st.text_area("Texte source", value=texte_propre[:2000] + "...", height=150, disabled=True)

        # ======================================================================
        # BRANCHE A : MODE ANGLAIS
        # ======================================================================
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
                        barre_progression.progress(
                            pct,
                            text=f"Traduction du grand bloc {i + 1}/{len(chunks_anglais)} (Clé #{index_cle + 1})..."
                        )

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

                            if "429" in erreur_str or "quota" in erreur_str or "resource_exhausted" in erreur_str:
                                diagnostic = "Plafond temporaire ou limite atteinte (429)"
                            elif "401" in erreur_str or "invalid authentication" in erreur_str:
                                diagnostic = "Clé invalide ou révoquée (401)"
                            elif "403" in erreur_str or "permission_denied" in erreur_str:
                                diagnostic = "Projet sans facturation active ou accès refusé (403)"
                            else:
                                diagnostic = f"Erreur de service ({str(e)[:120]})"

                            if index_cle + 1 < len(pool_cles):
                                st.warning(
                                    f"⚠️ **Clé #{index_cle + 1} écartée** : {diagnostic}. "
                                    f"Bascule immédiate sur la **Clé #{index_cle + 2}**...\n\n"
                                    f"**Erreur API Google :**\n```text\n{raw_error}\n```"
                                )
                                index_cle += 1
                                time.sleep(1.5)
                            else:
                                if chunks_traduits:
                                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                                st.error(
                                    f"🚨 **Échec définitif sur la Clé #{index_cle + 1}** : {diagnostic}.\n\n"
                                    f"**Erreur API Google :**\n```text\n{raw_error}\n```\n\n"
                                    "Toutes les clés du pool ont été consommées."
                                )
                                st.rerun()

                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                    st.rerun()

        # ======================================================================
        # BRANCHE B : MODE FRANÇAIS
        # ======================================================================
        elif mode_choisi == "🇫🇷 Document en Français":
            st.session_state.texte_pret_pour_audio = nettoyer_texte_pour_audio(texte_propre)

        # ======================================================================
        # ÉTAPE COMMUNE : AUDIO ET TÉLÉCHARGEMENT
        # ======================================================================
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
            if st.button("🎙️ Générer le Livre Audio HD", type="primary"):
                with st.spinner("🔊 Synthèse vocale fluide en cours avec Edge-TTS..."):
                    try:
                        texte_final_audio = nettoyer_texte_pour_audio(st.session_state.texte_pret_pour_audio)
                        donnees_audio_mp3 = generer_audio_hd(texte_final_audio, voix_technique)

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
