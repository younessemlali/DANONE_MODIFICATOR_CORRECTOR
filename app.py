import streamlit as st
import requests
import json
import re
import io
from lxml import etree
from copy import deepcopy

# ============================================================
# CONFIGURATION
# ============================================================
GITHUB_RAW_URL = "https://raw.githubusercontent.com/younessemlali/DANONE_MODIFICATOR_CORRECTOR/main/data/commandes.json"
NS = "http://ns.hr-xml.org/2004-08-02"

st.set_page_config(page_title="DANONE - Correcteur XML", layout="wide")
st.title("🔧 DANONE - Correcteur de modèle horaire XML")

# ============================================================
# CHARGEMENT DES COMMANDES GITHUB
# ============================================================
@st.cache_data(ttl=300)
def charger_commandes():
    try:
        r = requests.get(GITHUB_RAW_URL + "?t=" + str(int(__import__('time').time())))
        r.raise_for_status()
        data = r.json()
        commandes = data.get("commandes", [])
        # Dictionnaire numCommande -> modeleHoraire (dernière valeur gagne si doublon)
        return {c["numCommande"].lstrip("0") or c["numCommande"]: c["modeleHoraire"]
                for c in commandes if c.get("numCommande") and c.get("modeleHoraire")}
    except Exception as e:
        st.error(f"Erreur chargement GitHub : {e}")
        return {}

# Charger les données
col_refresh, col_info = st.columns([1, 4])
with col_refresh:
    if st.button("🔄 Rafraîchir les données"):
        st.cache_data.clear()
        st.rerun()

mapping = charger_commandes()

with col_info:
    if mapping:
        st.success(f"✅ {len(mapping)} commandes chargées depuis GitHub")
    else:
        st.error("❌ Aucune commande chargée depuis GitHub")

st.divider()

# ============================================================
# UPLOAD XML
# ============================================================
uploaded_file = st.file_uploader("📂 Charger un fichier XML de contrats", type=["xml"])

if not uploaded_file:
    st.info("Chargez un fichier XML pour commencer.")
    st.stop()

if not mapping:
    st.error("Impossible de corriger : aucune donnée chargée depuis GitHub.")
    st.stop()

# ============================================================
# ANALYSE DU XML
# ============================================================
raw_bytes = uploaded_file.read()

try:
    parser = etree.XMLParser(encoding="iso-8859-1", recover=True)
    tree = etree.fromstring(raw_bytes, parser)
except Exception as e:
    st.error(f"Erreur de lecture du XML : {e}")
    st.stop()

ns = {"hr": NS}

# Trouver tous les Assignment
assignments = tree.findall(".//hr:Assignment", ns)
if not assignments:
    # Essai sans namespace
    assignments = tree.findall(".//Assignment")

st.write(f"**{len(assignments)} contrat(s) trouvé(s) dans le fichier**")

# ============================================================
# CONSTRUCTION DU RAPPORT AVANT CORRECTION
# ============================================================
corrections = []  # liste de dicts {order_id, modele_actuel, modele_nouveau, statut}

for assignment in assignments:
    # Récupérer OrderId
    order_el = assignment.find(".//hr:OrderId/hr:IdValue", ns)
    if order_el is None:
        order_el = assignment.find(".//OrderId/IdValue")

    order_id_raw = order_el.text.strip() if order_el is not None else None
    if not order_id_raw:
        continue

    # Normaliser : enlever les zéros en tête pour la correspondance
    order_id_norm = order_id_raw.lstrip("0") or order_id_raw

    # Trouver la balise MODELE
    modele_el = None
    for el in assignment.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "IdValue" and el.get("name") == "MODELE":
            modele_el = el
            break

    modele_actuel = modele_el.text.strip() if modele_el is not None else "MANQUANT"

    # Chercher dans le mapping (avec et sans zéros)
    modele_nouveau = mapping.get(order_id_norm) or mapping.get(order_id_raw)

    if modele_nouveau:
        if modele_actuel == modele_nouveau:
            statut = "déjà_correct"
        else:
            statut = "à_corriger"
    else:
        statut = "commande_introuvable"

    corrections.append({
        "order_id": order_id_raw,
        "modele_actuel": modele_actuel,
        "modele_nouveau": modele_nouveau or "—",
        "statut": statut,
        "modele_el": modele_el
    })

# ============================================================
# AFFICHAGE DU RAPPORT
# ============================================================
a_corriger = [c for c in corrections if c["statut"] == "à_corriger"]
deja_correct = [c for c in corrections if c["statut"] == "déjà_correct"]
introuvable = [c for c in corrections if c["statut"] == "commande_introuvable"]

col1, col2, col3 = st.columns(3)
col1.metric("✅ À corriger", len(a_corriger))
col2.metric("⚪ Déjà correct", len(deja_correct))
col3.metric("⚠️ Commande introuvable", len(introuvable))

st.divider()

# Tableau des corrections
if a_corriger:
    st.subheader("📋 Corrections à appliquer")
    data_table = []
    for c in a_corriger:
        data_table.append({
            "N° Commande": c["order_id"],
            "Modèle actuel": c["modele_actuel"],
            "→ Nouveau modèle": c["modele_nouveau"]
        })
    st.dataframe(data_table, use_container_width=True, hide_index=True)

if introuvable:
    with st.expander(f"⚠️ {len(introuvable)} contrat(s) sans correspondance dans GitHub"):
        for c in introuvable:
            st.write(f"- Commande **{c['order_id']}** — modèle actuel : `{c['modele_actuel']}`")

if deja_correct:
    with st.expander(f"⚪ {len(deja_correct)} contrat(s) déjà corrects"):
        for c in deja_correct:
            st.write(f"- Commande **{c['order_id']}** — modèle : `{c['modele_actuel']}`")

st.divider()

# ============================================================
# BOUTON CORRECTION
# ============================================================
if not a_corriger:
    st.success("Aucune correction nécessaire.")
    st.stop()

if st.button("⚡ Appliquer les corrections", type="primary"):

    nb_corrections = 0
    contrats_corriges = []
    contrats_non_corriges = list(introuvable)  # déjà connus avant correction

    for assignment in assignments:
        order_el = assignment.find(".//hr:OrderId/hr:IdValue", ns)
        if order_el is None:
            order_el = assignment.find(".//OrderId/IdValue")
        if order_el is None:
            continue

        order_id_raw = order_el.text.strip()
        order_id_norm = order_id_raw.lstrip("0") or order_id_raw
        modele_nouveau = mapping.get(order_id_norm) or mapping.get(order_id_raw)
        if not modele_nouveau:
            continue

        modele_avant = None
        # Corriger toutes les balises MODELE de ce contrat
        for el in assignment.iter():
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "IdValue" and el.get("name") == "MODELE":
                if modele_avant is None:
                    modele_avant = el.text.strip() if el.text else "?"
                if el.text and el.text.strip() != modele_nouveau:
                    el.text = modele_nouveau
                    nb_corrections += 1

        contrats_corriges.append({
            "order_id": order_id_raw,
            "avant": modele_avant or "?",
            "apres": modele_nouveau
        })

    # Re-sérialiser en ISO-8859-1
    try:
        xml_corrige = etree.tostring(
            tree,
            encoding="iso-8859-1",
            xml_declaration=True,
            pretty_print=False
        )
    except Exception as e:
        st.error(f"Erreur lors de la sérialisation : {e}")
        st.stop()

    st.success(f"✅ {nb_corrections} balise(s) corrigée(s) dans {len(contrats_corriges)} contrat(s)")

    # Détail des contrats corrigés
    if contrats_corriges:
        st.subheader("✅ Contrats corrigés")
        st.dataframe(
            [{"N° Commande": c["order_id"], "Avant": c["avant"], "Après": c["apres"]} for c in contrats_corriges],
            use_container_width=True,
            hide_index=True
        )

    # Détail des contrats non corrigés
    if contrats_non_corriges:
        st.subheader("⚠️ Contrats non corrigés (commande absente du JSON GitHub)")
        st.dataframe(
            [{"N° Commande": c["order_id"], "Modèle actuel": c["modele_actuel"], "Raison": "Commande introuvable dans GitHub"} for c in contrats_non_corriges],
            use_container_width=True,
            hide_index=True
        )

    # Téléchargement
    nom_fichier = uploaded_file.name
    st.download_button(
        label="📥 Télécharger le XML corrigé",
        data=xml_corrige,
        file_name=nom_fichier,
        mime="application/xml"
    )
