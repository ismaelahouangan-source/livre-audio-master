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

# ==============================================================================
# CONFIGURATION DE LA PAGE & DES VOIX
# ==============================================================================
st.set_page_config(
    page_title="Studio Master - Édition DeepL & Voix HD",
    page_icon="⚡",
    layout="wide"
)

VOIX_FRANCAISES = {
    "Henri (Homme - Voix de narrateur grave)": "fr-FR-HenriNeural",
    "Denise (Femme - Douce & Naturelle)": "fr-FR-DeniseNeural",
    "Eloise (Femme - Dynamique & Claire)": "fr-FR-EloiseNeural",
    "Rémy (Homme - Clair & Standard)": "fr-FR-RemyNeural"
}

# ==============================================================================
# GESTION DU SESSION STATE
# ==============================================================================
def reinitialiser_memoire():
    st.session_state.texte_pret_pour_audio = None

if "texte_pret_pour_audio" not in st.session_state:
    st.session_state.texte_pret_pour_audio = None

# ==============================================================================
# EXTRACTION ET PRÉ-TRAITEMENT ÉDITORIAL (ANTI-NOTES)
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
    """
    Supprime les blocs de notes de bas de page et les appels de notes
    avant l'envoi à DeepL.
    """
    # 1. Élimination des sections entières de notes de bas de page en fin de document
    lignes = texte.split("\n")
    lignes_filtrees = []
    ignorer_fin = False

    pattern_section_notes = re.compile(
        r'^\s*(notes?\s+de\s+bas\s+de\s+page|footnotes?|notes?)\s*$', 
        re.IGNORECASE
    )

    for ligne in lignes:
        if pattern_section_notes.match(ligne):
            ignorer_fin = True
            break
        # Si une ligne démarre par un chiffre de note isolé (ex: "15. En raison du manque de place...")
        if re.match(r'^\s*\d{1,3}\.\s+[A-ZÀ-ÖØ-ß]', ligne) and len(lignes_filtrees) > 20:
            # On vérifie si les lignes précédentes ressemblaient déjà à des notes
            continue
        lignes_filtrees.append(ligne)

    texte_assaini = "\n".join(lignes_filtrees)

    # 2. Suppression des appels de notes numériques collés aux mots ou aux ponctuations (ex: "doctrine.15", "elohim.4")
    texte_assaini = re.sub(r'(?<=[a-zA-ZÀ-ÿ.,;:?!])\s*(\d{1,3})(?=\s|[.,;:?!]|$)', '', texte_assaini)

    # 3. Suppression des chiffres romains isolés en tête de document
    texte_assaini = re.sub(r'^\s*I\s*\n+', '', texte_assaini)

    return texte_assaini

def nettoyer_texte_source(texte: str) -> str:
    """Répare les césures de mots coupés et homogénéise les sauts de ligne."""
    texte = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', texte)
    texte = re.sub(r'(?<!\n)\n(?!\n)', ' ', texte)

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
    """
    Formate le texte final pour la synthèse vocale :
    - Corrige 'Job 38:4' en 'Job 38, 4' (supprime le bug de l'horloge).
    - Supprime tous les résidus Markdown.
    """
    if not texte:
        return ""

    # 1. Correction majeure des références bibliques (chiffre:chiffre -> chiffre, chiffre)
    texte = re.sub(r'(\d+):(\d+)', r'\1, \2', texte)

    # 2. Nettoyage Markdown
    texte = texte.replace("*", "")
    texte = re.sub(r'^#+\s*', '', texte, flags=re.MULTILINE)
    texte = re.sub(r'(?<=\s)_(?=\S)|(?<=\S)_(?=\s)', '', texte)
    texte = texte.replace("_", "")

    # 3. Ponctuation et tirets cadratins pour des pauses fluides
    texte = re.sub(r'\s*[—–]\s*', ', ', texte)
    texte = re.sub(r'^\s*[—–]\s*', '', texte, flags=re.MULTILINE)
    texte = texte.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')

    # 4. Préservation de la structure des paragraphes
    texte = re.sub(r'[ \t]+', ' ', texte)
    texte = re.sub(r' +(?=\n)', '', texte)
    texte = re.sub(r'\n\s*\n', '\n\n', texte)
    texte = re.sub(r'\n{3,}', '\n\n', texte)

    return texte.strip()

def decouper_texte_en_chunks(texte: str, taille_chunk: int = 15000) -> list:
    """
    Découpe le texte par paragraphes. DeepL accepte jusqu'à 128 Ko par requête,
    des tranches de 15 000 caractères réduisent considérablement les allers-retours réseau.
    """
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
    return (
        cle_brute.replace(r'\_', '_')
        .replace('\\', '')
        .strip()
        .strip('"')
        .strip("'")
    )

# ==============================================================================
# MOTEUR DE TRADUCTION DEEPL AVEC FAILOVER
# ==============================================================================
def traduire_chunk_deepl(chunk: str, api_key: str) -> str:
    """
    Appelle l'API DeepL. Détecte automatiquement s'il s'agit d'une clé Free (:fx)
    ou d'une clé Pro.
    """
    endpoint = "https://api-free.deepl.com/v2/translate" if api_key.endswith(":fx") else "https://api.deepl.com/v2/translate"
    
    headers = {
        "Authorization": f"DeepL-Auth-Key {api_key}"
    }
    
    payload = {
        "text": [chunk],
        "target_lang": "FR",
        "source_lang": "EN",
        "split_sentences": "nonewlines",  # Préserve les paragraphes et les retours à la ligne
        "preserve_formatting": "1"
    }
    
    response = requests.post(endpoint, headers=headers, data=payload, timeout=30)
    
    if response.status_code == 200:
        resultat = response.json()
        return resultat["translations"][0]["text"].strip()
    else:
        # Lève une exception détaillée pour la gestion d'erreurs
        raise Exception(f"HTTP {response.status_code} : {response.text}")

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
# INTERFACE UTILISATEUR
# ==============================================================================
def main():
    st.title("⚡ Le Studio Master - Édition DeepL")
    st.markdown("Pipeline haute vitesse : PyMuPDF ➡️ **DeepL API (Multi-Clés)** ➡️ Edge-TTS HD.")
    st.divider()

    cles_brutes = st.secrets.get("DEEPL_API_KEYS", None)
    if cles_brutes is None:
        cle_solo = st.secrets.get("DEEPL_API_KEY", "")
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
            st.caption(f"🔑 **{len(pool_cles)} clé(s) DeepL active(s)** dans le pool.")
            cle_manuelle = st.text_input("Remplacer temporairement par une clé DeepL :", type="password")
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

        st.success(f"✅ Extraction et nettoyage réussis ! ({len(texte_propre)} caractères prêts)")

        with st.expander("📄 Aperçu du texte préparé (sans notes)", expanded=False):
            st.text_area("Texte source nettoyé", value=texte_propre[:2000] + "...", height=150, disabled=True)

        # ======================================================================
        # BRANCHE A : MODE ANGLAIS (TRADUCTION DEEPL AVEC ROTATION)
        # ======================================================================
        if mode_choisi == "🇬🇧 Document en Anglais":
            if st.session_state.texte_pret_pour_audio is None:
                st.subheader("Étape 2 : Traduction en Français via DeepL")

                if st.button("🚀 Lancer la Traduction DeepL", type="primary"):
                    if not pool_cles:
                        st.error("🚨 Aucune clé API DeepL trouvée dans les Secrets ou la saisie manuelle.")
                        return

                    chunks_anglais = decouper_texte_en_chunks(texte_propre, taille_chunk=15000)
                    chunks_traduits = []
                    barre_progression = st.progress(0, text="Initialisation de DeepL...")

                    index_cle = 0
                    i = 0

                    while i < len(chunks_anglais):
                        pct = int(((i + 1) / len(chunks_anglais)) * 100)
                        barre_progression.progress(
                            pct,
                            text=f"Traduction partie {i + 1}/{len(chunks_anglais)} (Clé DeepL #{index_cle + 1}/{len(pool_cles)})..."
                        )

                        cle_active = pool_cles[index_cle]

                        try:
                            traduction_brute = traduire_chunk_deepl(chunks_anglais[i], cle_active)
                            traduction_propre = nettoyer_texte_pour_audio(traduction_brute)
                            chunks_traduits.append(traduction_propre)
                            i += 1
                            time.sleep(0.5)

                        except Exception as e:
                            erreur_str = str(e).lower()
                            raw_error = str(e)

                            # Détection des erreurs spécifiques à DeepL
                            if "456" in erreur_str or "quota exceeded" in erreur_str:
                                diagnostic = "Plafond mensuel de 1 000 000 caractères épuisé sur ce compte (HTTP 456)"
                            elif "403" in erreur_str or "forbidden" in erreur_str:
                                diagnostic = "Clé DeepL invalide ou non autorisée (HTTP 403)"
                            elif "429" in erreur_str:
                                diagnostic = "Trop de requêtes simultanées (HTTP 429)"
                            else:
                                diagnostic = f"Erreur de service ({str(e)[:120]})"

                            # Bascule automatique vers la clé suivante
                            if index_cle + 1 < len(pool_cles):
                                st.warning(
                                    f"⚠️ **Clé DeepL #{index_cle + 1} écartée** : {diagnostic}. "
                                    f"Bascule immédiate sur la **Clé #{index_cle + 2}**...\n\n"
                                    f"**Détails de l'erreur :**\n```text\n{raw_error}\n```"
                                )
                                index_cle += 1
                                time.sleep(1)
                            else:
                                if chunks_traduits:
                                    st.session_state.texte_pret_pour_audio = "\n\n".join(chunks_traduits)
                                st.error(
                                    f"🚨 **Échec définitif sur la Clé #{index_cle + 1}** : {diagnostic}.\n\n"
                                    f"**Détails de l'erreur :**\n```text\n{raw_error}\n```\n\n"
                                    "Toutes les clés DeepL enregistrées ont été consommées."
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
