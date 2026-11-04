"""Fingerprint of the E7 input document sets (read only): per parent run, document and chunk
counts and a sha256 over the sorted (chunk_id, sha256(text)) pairs."""
import hashlib, json, sys
from sqlalchemy import text
from water_source_research.db.session import get_engine
PARENTS = {"Houston": "b13e0d1d-aa03-471b-be3b-b63c93ceba9d",
           "Santiago": "b844390c-4e71-4635-8433-dc6122779573",
           "Vienna": "6f48e458-0700-489f-ab50-d50aa1f2a81f",
           "Nairobi": "21b46e42-5dc5-41fd-ad97-6f0db36a40cb"}
out = {}
with get_engine().connect() as c:
    for city, rid in PARENTS.items():
        docs = c.execute(text("select count(*), count(*) filter (where status='OK') from "
                              "source_documents where run_id=:r"), {"r": rid}).one()
        rows = c.execute(text("select chunk_id, text from evidence_chunks where run_id=:r "
                              "order by chunk_id"), {"r": rid}).all()
        h = hashlib.sha256()
        for cid, t in rows:
            h.update(f"{cid}:{hashlib.sha256(t.encode()).hexdigest()}\n".encode())
        claims = c.execute(text("select count(*) from field_claims where run_id=:r"),
                           {"r": rid}).scalar()
        out[city] = {"run_id": rid, "documents": docs[0], "documents_ok": docs[1],
                     "chunks": len(rows), "chunks_sha256": h.hexdigest(),
                     "parent_claims": claims}
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(json.dumps(out, indent=1))
