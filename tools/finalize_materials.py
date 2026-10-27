import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.tools'))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.tools/browsers'))
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

def main():
    out=ROOT/'tmp'/'slides';out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-proxy-server'])
        page=browser.new_page(viewport={'width':1280,'height':720},device_scale_factor=1)
        page.goto((ROOT/'delivery/02-答辩展示.html').as_uri(),wait_until='load')
        page.add_style_tag(content='@media screen{.slide{margin:0;box-shadow:none}body{background:#f8faf5}}html{scroll-behavior:auto}')
        issues=page.evaluate('''() => [...document.querySelectorAll('.slide')].map((s,i)=>({slide:i+1,contentBottom:s.querySelector('.body').getBoundingClientRect().bottom-s.getBoundingClientRect().top,footerTop:s.querySelector('footer').getBoundingClientRect().top-s.getBoundingClientRect().top})).filter(x=>x.contentBottom>x.footerTop-8)''')
        for i in range(12):
            page.evaluate('(i)=>window.scrollTo(0,i*720)',i)
            page.screenshot(path=str(out/f'slide-{i+1:02d}.png'))
        page.pdf(path=str(ROOT/'delivery/02-答辩展示.pdf'),print_background=True,prefer_css_page_size=True)
        browser.close()
    details=[]
    for path in sorted((ROOT/'delivery').glob('*.pdf')):
        reader=PdfReader(path)
        details.append({'file':path.name,'pages':len(reader.pages),'bytes':path.stat().st_size,'characters':sum(len(p.extract_text() or '') for p in reader.pages)})
    report={'slide_layout_issues':issues,'pdfs':details,'note':'Text and bounds checks complement visual PNG review.'}
    (ROOT/'results'/'artifact_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    if issues:raise SystemExit(1)

if __name__=='__main__':main()
