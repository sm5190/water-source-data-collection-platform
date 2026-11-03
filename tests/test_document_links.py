"""Outbound links of HTML pages: target, anchor, surrounding text, nearest heading and footer
flag (acquisition/links.py), stored per document insert-or-skip and copied with run evidence."""

from procedure_helpers import make_engine
from water_source_research.acquisition.http import parse_html
from water_source_research.services.evidence_store import EvidenceRepository

PAGE = b"""<html><head><title>City departments</title></head><body>
<h2>City Departments</h2>
<ul>
  <li><a href="https://www.examplewater.org/">Example Water</a> - drinking water and sewer</li>
  <li><a href="/parks">Parks and Recreation</a></li>
  <li><a href="mailto:info@example.gov">Email us</a></li>
</ul>
<p>Some <a href="#top">anchor</a> text.</p>
<footer><p>&copy; City of Example. <a href="https://www.partner.example/">Partner</a></p></footer>
</body></html>"""

def test_links_keep_anchor_context_heading_and_footer():
    doc = parse_html("https://www.examplecity.gov/departments", PAGE)
    links = {l["anchor_text"]: l for l in doc.links}
    water = links["Example Water"]
    assert water["target_host"] == "examplewater.org"
    assert water["heading"] == "City Departments"
    assert "drinking water and sewer" in water["context"]
    assert not water["in_footer"]
    assert links["Parks and Recreation"]["target_url"] == "https://www.examplecity.gov/parks"
    assert links["Partner"]["in_footer"]
    assert "Email us" not in links and "anchor" not in links  # mailto and #fragments skipped

def _doc_record(url):
    return {"requested_url": url, "canonical_url": url, "final_url": url, "status": "OK",
            "source_type": "GOVERNMENT", "chunks": 0}

def test_links_are_stored_once_and_copied_with_the_evidence(tmp_path):
    repo = EvidenceRepository(make_engine(tmp_path, "links.db"))
    url = "https://www.examplecity.gov/departments"
    repo.save_documents("run-a", [_doc_record(url)], [])
    links = parse_html(url, PAGE).links
    n = repo.save_document_links("run-a", {url: links})
    assert n == len(links) and repo.save_document_links("run-a", {url: links}) == 0
    stored = repo.document_links("run-a")[url]
    assert {l["anchor_text"] for l in stored} >= {"Example Water", "Partner"}
    assert all(l["basis"] == "PARSED" for l in stored)
    repo.copy_run_evidence("run-a", "run-b")
    assert len(repo.document_links("run-b")[url]) == n
