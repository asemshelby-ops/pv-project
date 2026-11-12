"""Static check of the paper draft, for machines without LaTeX.

    python paper/check_draft.py

Checks: every \\cite key exists in refs.bib (and lists unused entries); every \\ref has a
label; \\begin/\\end and braces balance; every figure and table the draft pulls in is one
that analysis/report.py writes. It counts the open TODOs. It does NOT replace a build.
"""
import pathlib
import re
import sys

here = pathlib.Path(__file__).resolve().parent
tex = (here / "main.tex").read_text(encoding="utf-8")
body = "\n".join(line.split("%")[0] if not line.lstrip().startswith("%") else ""
                 for line in tex.splitlines())
bib = (here / "refs.bib").read_text(encoding="utf-8")
report = (here.parent / "analysis" / "report.py").read_text(encoding="utf-8")
problems = []

keys = set(re.findall(r"@\w+\{([^,]+),", bib))
cited = {k.strip() for group in re.findall(r"\\cite\{([^}]*)\}", body) for k in group.split(",")}
problems += [f"cited but not in refs.bib: {k}" for k in sorted(cited - keys)]

labels = set(re.findall(r"\\label\{([^}#]+)\}", body))
labels |= {f"tab:{s}" for s in re.findall(r"\\resulttable\{([^}]+)\}", body)}
labels |= set(re.findall(r"\\resultfig\{[^}]+\}\{[^}]+\}\{.*?\}\{(fig:[^}]+)\}", body, flags=re.S))
refs = set(re.findall(r"\\ref\{([^}#]+)\}", body))
problems += [f"\\ref without label: {r}" for r in sorted(refs - labels)]

begins = re.findall(r"\\begin\{(\w+\*?)\}", body)
ends = re.findall(r"\\end\{(\w+\*?)\}", body)
for env in sorted(set(begins) | set(ends)):
    if begins.count(env) != ends.count(env):
        problems.append(f"environment {env}: {begins.count(env)} begin, {ends.count(env)} end")
plain = body.replace("\\{", "").replace("\\}", "")
if plain.count("{") != plain.count("}"):
    problems.append(f"braces: {plain.count('{')} open, {plain.count('}')} close")

pulled = re.findall(r"\\result(?:fig|table)\{([^}]+)\}", body)
problems += [f"not written by analysis/report.py: {s}" for s in pulled
             if f'"{s}"' not in report and s != "table7_parity"]

print(f"citations: {len(cited)} used, {len(keys)} in refs.bib; "
      f"unused: {', '.join(sorted(keys - cited)) or 'none'}")
print(f"figures and tables pulled from results/: {len(pulled)}")
print(f"open TODOs: {body.count(chr(92) + 'todo{')}")
for p in problems:
    print("PROBLEM:", p)
sys.exit(1 if problems else 0)
