"""Local code-map refresh only. No product edits, model APIs, installs or hooks.
Run using the Python environment that already contains graphify.
Outputs a new immutable snapshot below this handoff; existing graphs are untouched.
"""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[4]
ALLOWED = ('src','scripts','drizzle','tests','infra','.github','Homayar/backend','Homayar/frontend/src')
EXTENSIONS = {'.py','.ts','.tsx','.js','.jsx','.mjs','.cjs','.sh'}
EXCLUDED = {'node_modules','.venv','.git','__pycache__','.next','dist','out','sample','uploads','graphify-out','.pytest_cache','.ruff_cache','.mypy_cache'}

def main():
    from graphify.extract import extract
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all
    from graphify.analyze import god_nodes, surprising_connections, suggest_questions
    from graphify.report import generate
    from graphify.export import to_json, to_html
    from graphify.diagnostics import diagnose_extraction
    if not (ROOT/'Homayar/backend').is_dir() or not (ROOT/'src').is_dir():
        raise SystemExit('Expected connected two-repository Homayar workspace was not found; no writes performed.')
    files=[]
    for rel in ALLOWED:
        for p in (ROOT/rel).rglob('*'):
            if p.is_file() and not p.is_symlink() and p.suffix in EXTENSIONS and not EXCLUDED.intersection(p.parts) and not p.name.startswith('.env'):
                files.append(p)
    for rel in ('next.config.ts','eslint.config.mjs','playwright.config.ts','drizzle.config.ts','postcss.config.mjs','Homayar/frontend/vite.config.ts','Homayar/frontend/vitest.config.ts'):
        p=ROOT/rel
        if p.is_file():files.append(p)
    files=sorted(set(files))
    words=sum(len(p.read_text(errors='replace').split()) for p in files)
    print(f'Corpus: {len(files)} code files, approximately {words} words. No semantic API pass.')
    if not files or len(files)>500 or words>2000000:
        raise SystemExit('Empty/large source corpus: review and split explicitly before extraction.')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=Path(__file__).resolve().parent.parent/'graphify/refreshes'/stamp
    out.mkdir(parents=True,exist_ok=False)
    result=extract(files,cache_root=out/'cache',root=ROOT,max_workers=4)
    diag=diagnose_extraction(result,directed=True,root=ROOT)
    for name,value in [('extraction.json',result),('diagnostics.json',diag),('source-manifest.json',{'root':str(ROOT),'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},'external_llm_calls':0,'input_tokens':0,'output_tokens':0})]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    G=build_from_json(result,directed=True,root=ROOT)
    if not len(G):raise SystemExit('Empty graph; only diagnostic artifacts retained.')
    communities=cluster(G);cohesion=score_all(G,communities)
    labels={}
    for cid,ids in communities.items():
        counts=Counter(str(Path(G.nodes[n].get('source_file','')).parent) for n in ids)
        labels[cid]=(counts.most_common(1)[0][0] if counts else 'Unresolved')+f' · {cid}'
    if not to_json(G,communities,str(out/'graph.json'),community_labels=labels):raise SystemExit('Graph export rejected')
    report=generate(G,communities,cohesion,labels,god_nodes(G),surprising_connections(G,communities),{'total_files':len(files),'total_words':words,'files':{'code':[str(p) for p in files]}},{'input':0,'output':0},str(ROOT),suggested_questions=suggest_questions(G,communities,labels))
    (out/'GRAPH_REPORT.md').write_text('Local static code map; consult diagnostics.json and lossless raw extraction.json. No feature completion or definition-resolution guarantee.\n\n'+report)
    if len(G)>5000:print('WARNING: more than 5,000 nodes; HTML skipped. JSON/report retained.')
    else:to_html(G,communities,str(out/'graph.html'),community_labels=labels)
    flags={k:diag.get(k,0) for k in ('missing_endpoint_edges','dangling_endpoint_edges','self_loop_edges','directed_same_endpoint_collapsed_edges')}
    print(json.dumps({'output':str(out),'nodes':len(G),'edges':G.number_of_edges(),'integrity_warnings':flags},indent=2))
    print('Review warnings and source coverage before using this as the canonical map. Older outputs were preserved.')

if __name__=='__main__':main()
