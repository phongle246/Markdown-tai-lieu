"""Independent PDF producer for realism checks: LibreOffice renders a two-column chapter with real
automatic hyphenation, bullets in a separate font, a merged-cell table, header/footer fields and an image."""
import base64
import shutil
import subprocess
from pathlib import Path

import fixtures

HAVE_SOFFICE = shutil.which("soffice") is not None

SENT = ("Thalassemia is an inherited disorder of hemoglobin synthesis characterized by reduced production of globin chains. "
        "Patients with beta-thalassemia major present in the first two years of life with severe anemia, hepatosplenomegaly, and failure to thrive. "
        "The prevalence of the carrier state reaches 5–10% in some Mediterranean populations,<text:span text:style-name=\"Sup\">1</text:span> and the long-term outcome depends on adherence to transfusion and chelation therapy. "
        "Deferasirox is given at 20–30 mg/kg/day orally, and the ferritin target is below 1,000 ng/mL [2]. ")
EXPECTED_SENTENCE = "Deferasirox is given at 20–30 mg/kg/day orally, and the ferritin target is below 1,000 ng/mL [2]."


def P(t, style="Body"):
    return f'<text:p text:style-name="{style}">{t}</text:p>'


def build_fodt() -> str:
    img = base64.b64encode(fixtures.png_bytes(300, 160)).decode()
    paras = ""
    for sec, (h, n) in enumerate([("Epidemiology", 3), ("Pathophysiology", 3), ("Clinical Manifestations", 4)]):
        paras += P(h, "H2")
        for _ in range(n):
            paras += P(SENT * 2)
        if sec == 1:
            paras += P("Genotype–phenotype correlation", "H3") + P(SENT)
            paras += ('<text:list text:style-name="LBullet">' + "".join(
                f"<text:list-item>{P(t)}</text:list-item>" for t in (
                    "Transfusion every 3–4 weeks to keep pre-transfusion Hb above 9.5 g/dL",
                    "Chelation with deferasirox 20–30 mg/kg/day", "Monitor ferritin and cardiac T2* yearly")) + "</text:list>")
            paras += ('<text:list text:style-name="LNum">' + "".join(
                f"<text:list-item>{P(t)}</text:list-item>" for t in ("Confirm the diagnosis by hemoglobin electrophoresis", "Start regular transfusions")) + "</text:list>")
        if sec == 2:
            cell = lambda t, st="TD", extra="": f'<table:table-cell table:style-name="Cell"{extra}><text:p text:style-name="{st}">{t}</text:p></table:table-cell>'
            paras += P('<text:span text:style-name="Bold">Table 1.</text:span> Hematologic findings in thalassemia syndromes', "Caption")
            paras += ('<table:table table:name="T1" table:style-name="Tbl"><table:table-column table:number-columns-repeated="4"/>'
                      '<table:table-header-rows><table:table-row>' + cell("Syndrome", "TH") + cell("Hematology", "TH", ' table:number-columns-spanned="2"')
                      + "<table:covered-table-cell/>" + cell("Hb (g/dL)", "TH") + "</table:table-row></table:table-header-rows>"
                      "<table:table-row>" + cell("Minor") + cell("MCV 60–70") + cell("MCH 19–22") + cell("9.5–12.0") + "</table:table-row>"
                      "<table:table-row>" + cell("Major") + cell("MCV 50–60") + cell("MCH 14–18") + cell("&lt;7.0") + "</table:table-row></table:table>")
            paras += P("")
            paras += P(f'<draw:frame draw:style-name="Fr" draw:name="img1" text:anchor-type="paragraph" svg:width="7cm" svg:height="3.7cm"><draw:image><office:binary-data>{img}</office:binary-data></draw:image></draw:frame>', "Center")
            paras += P('<text:span text:style-name="Bold">Figure 1.</text:span> Peripheral blood smear showing microcytosis and target cells.', "Caption")
    paras += P("References", "H2")
    for i, a in enumerate(["Cappellini MD, Cohen A, Porter J, et al. Guidelines for the Management of Transfusion Dependent Thalassaemia. 3rd ed. TIF; 2014.",
                           "Taher AT, Weatherall DJ, Cappellini MD. Thalassaemia. Lancet. 2018;391:155–167. doi:10.1016/S0140-6736(17)31822-6",
                           "Origa R. β-Thalassemia. Genet Med. 2017;19:609–619. PMID: 27811859"], 1):
        paras += P(f"{i}. {a}", "Ref")
    bullets = lambda name, kind, fmt: (f'<text:list-style style:name="{name}">' + kind + "</text:list-style>")
    lp = '<style:list-level-properties text:list-level-position-and-space-mode="label-alignment"><style:list-level-label-alignment text:label-followed-by="listtab" text:list-tab-stop-position="0.9cm" fo:text-indent="-0.5cm" fo:margin-left="0.9cm"/></style:list-level-properties>'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.text">
<office:font-face-decls><style:font-face style:name="Liberation Serif" svg:font-family="'Liberation Serif'"/></office:font-face-decls>
<office:styles>
<style:default-style style:family="paragraph"><style:text-properties style:font-name="Liberation Serif" fo:font-size="10pt" fo:language="en" fo:country="US" fo:hyphenate="true" fo:hyphenation-remain-char-count="3" fo:hyphenation-push-char-count="3"/></style:default-style>
<style:style style:name="Body" style:family="paragraph"><style:paragraph-properties fo:text-align="justify" fo:margin-bottom="0.15cm" fo:text-indent="0.4cm"/></style:style>
<style:style style:name="Ref" style:family="paragraph"><style:paragraph-properties fo:text-align="start" fo:margin-bottom="0.1cm"/><style:text-properties fo:font-size="8.5pt"/></style:style>
<style:style style:name="Center" style:family="paragraph"><style:paragraph-properties fo:text-align="center"/></style:style>
<style:style style:name="Caption" style:family="paragraph"><style:paragraph-properties fo:margin-bottom="0.2cm" fo:margin-top="0.1cm"/><style:text-properties fo:font-size="8.5pt"/></style:style>
<style:style style:name="Title" style:family="paragraph"><style:paragraph-properties fo:margin-bottom="0.3cm"/><style:text-properties fo:font-size="22pt" fo:font-weight="bold"/></style:style>
<style:style style:name="H2" style:family="paragraph"><style:paragraph-properties fo:margin-top="0.3cm" fo:margin-bottom="0.15cm" fo:keep-with-next="always"/><style:text-properties fo:font-size="14pt" fo:font-weight="bold"/></style:style>
<style:style style:name="H3" style:family="paragraph"><style:paragraph-properties fo:margin-top="0.2cm" fo:margin-bottom="0.1cm" fo:keep-with-next="always"/><style:text-properties fo:font-size="11pt" fo:font-weight="bold" fo:font-style="italic"/></style:style>
<style:style style:name="TH" style:family="paragraph"><style:text-properties fo:font-size="8.5pt" fo:font-weight="bold"/></style:style>
<style:style style:name="TD" style:family="paragraph"><style:text-properties fo:font-size="8.5pt"/></style:style>
<style:style style:name="Bold" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="Sup" style:family="text"><style:text-properties style:text-position="super 58%"/></style:style>
<style:style style:name="Header" style:family="paragraph"><style:text-properties fo:font-size="8pt" fo:font-style="italic"/></style:style>
<text:list-style style:name="LBullet"><text:list-level-style-bullet text:level="1" text:bullet-char="•">{lp}</text:list-level-style-bullet></text:list-style>
<text:list-style style:name="LNum"><text:list-level-style-number text:level="1" style:num-suffix="." style:num-format="1">{lp}</text:list-level-style-number></text:list-style>
</office:styles>
<office:automatic-styles>
<style:style style:name="Tbl" style:family="table"><style:table-properties style:width="8cm" table:align="left" fo:margin-bottom="0.2cm"/></style:style>
<style:style style:name="Cell" style:family="table-cell"><style:table-cell-properties fo:padding="0.08cm" fo:border="0.5pt solid #000000"/></style:style>
<style:style style:name="Fr" style:family="graphic"><style:graphic-properties style:wrap="none" style:horizontal-pos="center" style:horizontal-rel="paragraph"/></style:style>
<style:page-layout style:name="PL"><style:page-layout-properties fo:page-width="21.59cm" fo:page-height="27.94cm" fo:margin-left="1.9cm" fo:margin-right="1.9cm" fo:margin-top="1.2cm" fo:margin-bottom="1.2cm"><style:columns fo:column-count="2" fo:column-gap="0.6cm"/></style:page-layout-properties>
<style:header-style><style:header-footer-properties fo:min-height="0.6cm" fo:margin-bottom="0.5cm"/></style:header-style><style:footer-style><style:header-footer-properties fo:min-height="0.5cm" fo:margin-top="0.4cm"/></style:footer-style></style:page-layout>
</office:automatic-styles>
<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="PL"><style:header><text:p text:style-name="Header">CHAPTER 1 ■ Thalassemia <text:tab/>Page <text:page-number/></text:p></style:header><style:footer><text:p text:style-name="Header">Copyright © 2024 Elsevier Inc. All rights reserved.</text:p></style:footer></style:master-page></office:master-styles>
<office:body><office:text><text:p text:style-name="Title">Chapter 1 Thalassemia</text:p>{paras}</office:text></office:body></office:document>"""


def make_libreoffice_pdf(workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "thal.fodt"
    src.write_text(build_fodt(), encoding="utf-8")
    subprocess.run(["soffice", "--headless", f"-env:UserInstallation=file://{workdir}/lo_profile", "--convert-to", "pdf",
                    "--outdir", str(workdir), str(src)], check=True, capture_output=True, timeout=180)
    return workdir / "thal.pdf"
