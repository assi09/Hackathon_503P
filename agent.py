#!/usr/bin/env python3
"""Required entry point: Python 3.11+, no browser/system installation required."""
import argparse
import hashlib
import json
import re
import signal
import sys
import time
from pathlib import Path

from p2p.client import Client
from p2p.prompts import AUDIT, GENERATE
from p2p.render import render
from p2p.runtime import check_schema, normalize_invariants, normalize_spec, validate
from p2p.source import load_case

STARTED = time.monotonic()


class Trace:
    def __init__(self, path):
        self.file = path.open('w', encoding='utf-8')

    def __call__(self, stage, action, result):
        event = {'stage': stage, 'action': action, 'result': result,
                 'elapsed_seconds': round(time.monotonic() - STARTED, 3)}
        line = json.dumps(event, ensure_ascii=False, allow_nan=False)
        # Defense in depth: no credentials even in model/API error strings.
        line = re.sub(r'sk-or-v1-[A-Za-z0-9_-]+', '[REDACTED]', line)
        self.file.write(line + '\n')
        self.file.flush()

    def close(self):
        self.file.close()


def save_candidate(out, spec, case, checks, trace):
    (out / 'candidate.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    if check_schema(spec):
        return False
    page = render(spec, case, checks)
    temp = out / 'index.html.tmp'
    temp.write_text(page, encoding='utf-8')
    temp.replace(out / 'index.html')
    (out / 'lesson.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    trace('artifact', 'write', {'file': 'index.html', 'bytes': len(page.encode()),
          'sha256': hashlib.sha256(page.encode()).hexdigest(), 'self_contained': True})
    return True


def apply_patch(spec, patch):
    if not isinstance(patch, dict):
        raise ValueError('Review patch must be an object')
    allowed = {'title','subtitle','intro','plan','equation','symbols','source','controls',
               'compute','explorations','limitations','tests'}
    if set(patch) - allowed:
        raise ValueError('Review supplied unknown top-level lesson fields')
    return {**spec, **patch}


def run(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    trace = Trace(out / 'trace.jsonl')
    client = None
    completed, artifact = False, False
    try:
        trace('start','configuration',{'model':args.model,'python':sys.version.split()[0],
              'limits':{'seconds':550,'requests':9,'completion_tokens':29000}})
        case, provenance = load_case(Path(args.input))
        trace('source','read_excerpt',provenance)
        client = Client(args.model,trace,STARTED)
        case_text = json.dumps(case, ensure_ascii=False)
        # One compact generation combines observable planning and construction.
        spec = client.ask('generate', GENERATE, 'Create the lesson for this input:\n'+case_text, 7200)
        spec, normalization = normalize_spec(spec,case.get('section'))
        if normalization:
            trace('revise','normalize_widget_names',{'changes':normalization})
        trace('plan','teaching_decisions',{'plan':spec.get('plan',[]),
              'title':spec.get('title'),'controls':[c.get('id') for c in spec.get('controls',[]) if isinstance(c,dict)]})
        checks = validate(spec)
        trace('check','execute_javascript',{'checks':checks,'engine':'QuickJS NG','same_code_as_page':True})
        artifact = save_candidate(out,spec,case,checks,trace)
        review = client.ask('review', AUDIT, json.dumps({'input':case,'lesson':spec,
            'execution_failures':[c for c in checks if not c['passed']]},ensure_ascii=False), 4800)
        reference = review.get('reference_tests',[])
        invariants = normalize_invariants(review.get('invariants',[]))
        if not isinstance(invariants,list) or not invariants:
            raise ValueError('Independent review did not supply scientific invariants')
        if not isinstance(reference,list) or len(reference)<3:
            raise ValueError('Independent review did not supply three analytic reference tests')
        if review.get('verdict') not in {'pass','revise'}:
            raise ValueError('Independent review returned invalid verdict')
        trace('review','scientific_findings',{'verdict':review['verdict'],
              'findings':review.get('findings',[]),'reference_tests':reference,'invariants':invariants})
        patch = review.get('patch',{})
        pending_review = review.get('findings',[]) if review['verdict']=='revise' and not patch else []
        if patch:
            spec = apply_patch(spec,patch)
            spec, normalization = normalize_spec(spec,case.get('section'))
            if normalization:
                trace('revise','normalize_widget_names',{'changes':normalization})
            trace('revise','apply_review_patch',{'fields':list(patch)})
        elif review['verdict']=='revise':
            trace('review','correction_required',{'findings':pending_review})
        for attempt in range(3):
            checks = validate(spec,reference,invariants)
            if pending_review:
                checks.append({'name':'unresolved_scientific_review','passed':False,'findings':pending_review})
            trace('check','execute_javascript',{'round':attempt+1,'checks':checks,
                  'independent_reference_tests':len(reference),'engine':'QuickJS NG'})
            artifact = save_candidate(out,spec,case,checks,trace) or artifact
            failures = [c for c in checks if not c['passed']]
            if not failures:
                completed = True
                break
            if attempt == 2:
                break
            patch = client.ask('repair', GENERATE + '\nREPAIR OVERRIDE (takes precedence over the generation response format above): You repair a scientific lesson using concrete execution failures. '
                'Return JSON {"patch":{complete replacement top-level lesson fields},"changes":[brief descriptions],"corrected_reference_tests":[],"reference_corrections":[],"corrected_invariants":[],"invariant_corrections":[]}. '
                'Inspect the failed expression and actual outputs first. A reviewer can make a test bug (such as a missing term in an equality). When the implementation matches the source but the invariant does not, correct the invariant with explicit mathematical evidence; do not distort correct calculations. Return ONLY changed top-level fields inside patch, not the entire lesson. Keep the existing output schema and control IDs so independent reference tests remain valid. If a scientific invariant has an incorrect precondition or contradicts the source, supply the complete corrected_invariants list and invariant_corrections explaining the source-based correction. Never weaken a correct invariant to hide an implementation error. '
                'Do NOT alter expected values to hide computation errors. However, an incorrect analytic reference may be corrected if you include the full replacement reference_tests list in corrected_reference_tests, and reference_corrections giving each wrong test name, the source equation, a short explicit numerical derivation, and why the old expectation was wrong. Only correct a reference when it contradicts the supplied source. Likewise correct a generator test if its expected value contradicts the source. Fix the actual bug, not the test to make buggy code pass. No private reasoning.',
                json.dumps({'input':case,'lesson':spec,'failures':failures,
                    'reference_tests':reference,'invariants':invariants},ensure_ascii=False),6500)
            if pending_review and not patch.get('patch'):
                raise ValueError('Scientific findings remain unresolved: repair returned no correction')
            pending_review = []
            spec = apply_patch(spec,patch.get('patch',{}))
            spec, normalization = normalize_spec(spec,case.get('section'))
            if normalization:
                trace('revise','normalize_widget_names',{'changes':normalization})
            if patch.get('corrected_invariants'):
                if not patch.get('invariant_corrections'):
                    raise ValueError('Invariant correction needs explicit source-based evidence')
                invariants = normalize_invariants(patch['corrected_invariants'])
                trace('review','invariant_correction',{'evidence':patch['invariant_corrections'],'invariants':invariants})
            if patch.get('corrected_reference_tests'):
                if len(patch['corrected_reference_tests']) < 3 or not patch.get('reference_corrections'):
                    raise ValueError('Reference correction needs three tests and explicit source-based evidence')
                reference = patch['corrected_reference_tests']
                trace('review','reference_correction',{'evidence':patch['reference_corrections'],'reference_tests':reference})
            trace('revise','apply_execution_repair',{'fields':list(patch.get('patch',{})),
                  'changes':patch.get('changes',[])})
        trace('finish','summary',{'success':completed,'usable_candidate_written':artifact,
            'requests':client.calls,'prompt_tokens':client.prompt_tokens,
            'completion_tokens':client.completion_tokens,
            'total_tokens':client.prompt_tokens+client.completion_tokens,
            'usage_verified':client.usage_verified,
            'unverified_completion_reservation':client.reserved_unknown,
            'checks_passed':sum(c['passed'] for c in checks),'checks_total':len(checks)})
        if completed:
            print(f'Created {out / "index.html"} | {len(checks)} checks passed | '
                  f'{client.calls} API requests | {client.prompt_tokens+client.completion_tokens} tokens | '
                  f'{time.monotonic()-STARTED:.1f}s')
            return 0
        print('Checks still failing; preserved candidate and trace for inspection.',file=sys.stderr)
        return 1
    except Exception as exc:
        error = re.sub(r'sk-or-v1-[A-Za-z0-9_-]+','[REDACTED]',str(exc))[:1500]
        trace('failure','stop',{'type':type(exc).__name__,'message':error,
              'usable_candidate_written':artifact,'requests':client.calls if client else 0,
              'prompt_tokens':client.prompt_tokens if client else 0,
              'completion_tokens':client.completion_tokens if client else 0,
              'usage_verified':client.usage_verified if client else True})
        print(error,file=sys.stderr)
        return 1
    finally:
        trace.close()


def timeout_handler(signum, frame):
    raise TimeoutError('Stopped before the ten-minute assessment deadline')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--model',required=True)
    args = parser.parse_args()
    if hasattr(signal,'SIGALRM'):
        signal.signal(signal.SIGALRM,timeout_handler)
        signal.alarm(max(1,int(550-(time.monotonic()-STARTED))))
    sys.exit(run(args))
