# Milestone 3 P0 — stored-run discovery findings (read only)

Source: `scripts/discovery_audit.py` on the five fresh research runs (discovery_audit.md/json here).
Extraction calls are linked to documents through the bundles created during each run (time window,
per field, only where bundle and slot counts match; all calls linked).

- **Santiago 0d7d4af8 (49 calls):** almarwater.com classed UTILITY in round 1 (found by
  "Santiago Chile water supply aqueduct pipeline transfer"); it became the round-2 utility host
  together with riotimesonline.com (a news site, also classed UTILITY) and aguashorizonte.cl.
  3 site:almarwater.com searches; 5 Almar pages + 1 login page acquired; Almar pages were the
  only evidence in 16 of 49 calls. Bare-city Spanish queries reached conagua.gob.mx (Mexico,
  fetch failed), ins.gov.co and minvivienda.gov.co (Colombia).
- **Houston 993b3415 (43 calls):** msdh.ms.gov (Mississippi State Department of Health — Houston,
  Mississippi, a homonym) fed 19 calls, 4 with it as the only source; leadingutilities.org and
  waterutility.ai (directories) were UTILITY and became utility hosts; houstonpublicworks.org
  (the city's utility department) was classed TECHNICAL.
- **Vienna e800f614 (50 calls):** viennava.gov (Vienna, Virginia — a homonym) fed 14 calls;
  corporate pages borealisgroup.com (5 sole-source calls), omv.com, verbund.com were acquired.
- **Luanda 80aafe20:** worldwater.org classed UTILITY (round-2 utility host); eni.com corporate report.
- **Nairobi f622ab6a:** nairobiwater.co.ke utility host (correct); oloolaiserwatercompany.co.ke (another
  town's company) acquired.
