"""Development-only real Chromium checks. Not required at assessment runtime.
Install playwright separately; use an existing Chrome/Chromium executable.
"""
import argparse
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright


def check(path, chrome):
    findings=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chrome,headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1100},offline=True)
        page=context.new_page(); errors=[]; requests=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url) if r.url.startswith(('http:','https:')) else None)
        page.goto(path.resolve().as_uri())
        page.wait_for_function('() => window.__playground && window.__playground.ready',timeout=10000)
        spec=page.eval_on_selector('#lesson-data','e=>JSON.parse(e.textContent)')
        findings.append({'check':'offline_page_load','passed':True})
        def wait_revision(old):
            page.wait_for_function('(old)=>window.__playground.ready&&window.__playground.revision>old',arg=old)
        # Exercise actual DOM controls and require a numerical change.
        initial=page.evaluate('window.__playground.lastOutput')
        for c in spec['controls']:
            old=page.evaluate('window.__playground.revision')
            if c['type']=='number':
                locator=page.get_by_role('spinbutton',name=c['label'],exact=True)
                v=c['min'] if c['value']!=c['min'] else c['max']
                locator.fill(str(v));locator.dispatch_event('input')
            elif c['type']=='toggle':page.get_by_role('checkbox',name=c['label'],exact=True).click()
            elif c['type']=='select':
                option=next(o['value'] for o in c['options'] if o['value']!=c['value'])
                page.get_by_role('combobox',name=c['label'],exact=True).select_option(option)
            else:
                name=c['label']+(' row 1 column 1' if c['type']=='matrix' else ' 1')
                value=c['value'][0][0] if c['type']=='matrix' else c['value'][0]
                v=c['max'] if value!=c['max'] else c['min']
                page.get_by_role('spinbutton',name=name,exact=True).fill(str(v))
            wait_revision(old)
            after=page.evaluate('window.__playground.lastOutput')
            before_numeric={'metrics':initial['metrics'],'panels':initial['panels']}
            after_numeric={'metrics':after['metrics'],'panels':after['panels']}
            affected=before_numeric!=after_numeric
            # A toggle may act only on a changed input (e.g. normalizing weights
            # that already sum to one is intentionally a no-op).
            if not affected and c['type']=='toggle':
                for other in spec['controls']:
                    if other['type'] not in {'number','vector','matrix'}:continue
                    name=other['label']+(' row 1 column 1' if other['type']=='matrix' else ' 1' if other['type']=='vector' else '')
                    old=page.evaluate('window.__playground.revision')
                    page.get_by_role('spinbutton',name=name,exact=True).fill(str(other['max']));wait_revision(old)
                    contextual=page.evaluate('window.__playground.lastOutput')
                    old=page.evaluate('window.__playground.revision')
                    page.get_by_role('checkbox',name=c['label'],exact=True).click();wait_revision(old)
                    toggled=page.evaluate('window.__playground.lastOutput')
                    affected=any(contextual[k]!=toggled[k] for k in ['metrics','panels'])
                    if affected:break
            findings.append({'check':'DOM control: '+c['id'],'passed':affected})
            old=page.evaluate('window.__playground.revision');page.get_by_role('button',name='Reset all inputs').click();wait_revision(old)
        for c in spec['controls']:
            if c['type']=='vector' and c.get('maxItems',0)>c.get('minItems',0):
                old_state=page.evaluate('window.__playground.getState()')
                old=page.evaluate('window.__playground.revision')
                plus=page.get_by_role('button',name='Add entry to '+c['label'],exact=True)
                minus=page.get_by_role('button',name='Remove entry from '+c['label'],exact=True)
                (plus if plus.is_enabled() else minus).click();wait_revision(old)
                new_state=page.evaluate('window.__playground.getState()')
                findings.append({'check':'DOM vector length: '+c['id'],
                    'passed':len(old_state[c['id']])!=len(new_state[c['id']])})
                old=page.evaluate('window.__playground.revision');page.get_by_role('button',name='Reset all inputs').click();wait_revision(old)
        for i in range(2):
            old=page.evaluate('window.__playground.revision');page.get_by_role('button',name='Try this experiment').nth(i).click();wait_revision(old)
            findings.append({'check':f'experiment {i+1}','passed':page.locator('#error').is_hidden()})
        page.get_by_role('button',name='Reset all inputs').click()
        page.wait_for_function('() => window.__playground.ready')
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(path.parent/'desktop.png'),full_page=True)
        for width in [390,768]:
            page.set_viewport_size({'width':width,'height':844})
            overflow=page.evaluate('document.documentElement.scrollWidth>window.innerWidth+1')
            findings.append({'check':f'no horizontal page overflow at {width}px','passed':not overflow})
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(path.parent/'mobile.png'),full_page=True)
        findings.extend([{'check':'no browser errors','passed':not errors,'errors':errors},
                         {'check':'no external requests','passed':not requests,'requests':requests}])
        # Also check the required local HTTP serving mode.
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(path.parent.resolve())))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        served=browser.new_context()
        served.route('**/*',lambda route:route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort())
        tab=served.new_page()
        try:
            tab.goto(f'http://127.0.0.1:{server.server_port}/{path.name}')
            tab.wait_for_function('() => window.__playground && window.__playground.ready',timeout=10000)
            findings.append({'check':'locally served in Chromium','passed':True})
        finally:
            served.close();server.shutdown();server.server_close()
        browser.close()
    (path.parent/'browser-checks.json').write_text(json.dumps(findings,indent=2))
    print(json.dumps(findings,indent=2))
    return all(f['passed'] for f in findings)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('page',type=Path)
    parser.add_argument('--chrome',default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    args=parser.parse_args();sys.exit(0 if check(args.page,args.chrome) else 1)
