import re
from pathlib import Path

p = Path(r"Suites/Accountdashboardregression_Regression.xml")
text = p.read_text(encoding="utf-8")
stripped = re.sub(r"<!--.*?-->", "", text, flags=re.S)
classes = re.findall(r'<class name="([^"]+)"', stripped)
print("LIVE COUNT:", len(classes))
for c in classes:
    print(c)
print("--- other suites ---")
for name in [
    "Programaccountregression_Regression.xml",
    "Memberregistrationregression_Regression.xml",
    "Membervalidationregression_Regression.xml",
]:
    t = Path("Suites/" + name).read_text(encoding="utf-8")
    s = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    cs = re.findall(r'<class name="([^"]+)"', s)
    print(name, "LIVE", len(cs))
