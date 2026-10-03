import argparse
import contextlib
import io
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from p2p.client import BudgetError, Client, parse_object
from p2p.render import render
from p2p.runtime import Engine, normalize_spec, validate
from p2p.source import load_case


def fixture():
    # A synthetic arithmetic fixture, never used by the generator.
    return {'title':'Linear relationship','subtitle':'Vary slope and input','intro':'A test fixture.',
        'equation':'y = a × x','plan':['Show product'],'symbols':[{'symbol':'a','meaning':'slope'}],
        'source':{'title':'Synthetic fixture','locator':'definition','supported':['y = a x'],
                  'simplifications':['Small numbers']},
        'controls':[{'id':'a','label':'Slope','help':'Multiplier','type':'number','value':2,'min':-5,'max':5,'step':1},
                    {'id':'x','label':'Input','help':'Input coordinate','type':'number','value':3,'min':-5,'max':5,'step':1}],
        'compute':'''function compute(s){const y=s.a*s.x;return {metrics:[{label:'Output',value:y,unit:''}],
          panels:[{kind:'bar',title:'Output',description:'Computed product',labels:['y'],values:[y],unit:''}],
          steps:[{label:'Input',formula:'x',value:s.x,explanation:'Read x'},
                 {label:'Product',formula:'a × x',value:y,explanation:'Multiply'}],message:'A product'};}''',
        'explorations':[{'title':'Zero','prediction':'What if x=0?','action':'Set x to zero',
          'state':{'x':0},'observe':'Zero output','why':'Zero times any number is zero'},
          {'title':'Sign','prediction':'What if slope is negative?','action':'Set a=-2',
           'state':{'a':-2},'observe':'Negative output','why':'A negative times a positive is negative'}],
        'limitations':['Synthetic fixture only'],
        'tests':[{'name':'known product','state':{},'expect':[{'path':'metrics.0.value','value':6}]}]}


class CoreTests(unittest.TestCase):
    def test_executes_real_math_and_boundaries(self):
        results=validate(fixture(),[{'name':'independent negative product','state':{'x':-3},
                                  'expect':[{'path':'panels.0.values.0','value':-6}]}])
        self.assertTrue(all(x['passed'] for x in results),results)
        self.assertGreater(len(results),15)

    def test_independent_formula_and_invariants(self):
        results=validate(fixture(), [{'name':'source formula','state':{'x':-3},
            'expect':[{'path':'metrics.0.value','formula':'s.a * s.x'}]}],
            [{'name':'product identity','expression':'o.metrics[0].value === s.a*s.x'}])
        self.assertTrue(all(x['passed'] for x in results),results)
        bad=validate(fixture(),invariants=['o.metrics[0].value === 999'])
        self.assertTrue(any(not x['passed'] for x in bad))

    def test_normalizes_slider_without_changing_math(self):
        s=fixture();s['controls'][0]['type']='slider'
        s,changes=normalize_spec(s)
        self.assertEqual(s['controls'][0]['type'],'number')
        self.assertEqual(len(changes),1)
        self.assertEqual(Engine(s).run()['metrics'][0]['value'],6)

    def test_undefined_metric_is_explicit_and_plot_can_be_unavailable(self):
        s=fixture()
        s['compute']=s['compute'].replace("value:y,unit:''", "value:null,defined:false,reason:'Undefined at this state',unit:''")
        s['compute']=s['compute'].replace("description:'Computed product',labels", "description:'Computed product',unavailable:'Undefined at this state',labels")
        from p2p.runtime import output_errors
        self.assertEqual(output_errors(Engine(s).run()),[])

    def test_budget_stops_before_spending(self):
        with patch.dict('os.environ',{'OPENROUTER_API_KEY':'test-secret'}):
            c=Client('test',lambda *x:None,time.monotonic())
            c.completion_tokens=28900
            with self.assertRaises(BudgetError):c.ask('test','s','u')
            c.completion_tokens=0;c.deadline=time.monotonic()+2
            with self.assertRaises(BudgetError):c.ask('test','s','u')

    def test_missing_review_patch_triggers_autonomous_repair(self):
        import agent
        lesson=fixture()
        reference=[{'name':'reference '+str(x),'state':{'x':x},
            'expect':[{'path':'metrics.0.value','value':2*x}]} for x in [0,1,-2]]
        responses=[lesson,{'verdict':'revise','findings':['Explain the meaning of the slope.'],
            'reference_tests':reference,'invariants':[{'name':'product','expression':'o.metrics[0].value===s.a*s.x'}],
            'patch':{}},{'patch':{'intro':'Slope a multiplies the input x to give output y.'},'changes':['Defined slope.']}]
        class FakeClient:
            def __init__(self,*args):
                self.calls=0;self.prompt_tokens=0;self.completion_tokens=0;self.reserved_unknown=0;self.usage_verified=True
            def ask(self,*args):
                r=copy.deepcopy(responses[self.calls]);self.calls+=1;return r
        with tempfile.TemporaryDirectory() as d:
            case=Path(d)/'case.json';out=Path(d)/'out'
            case.write_text(json.dumps({'source_url':'https://example.org','focus':'product','audience':'student','excerpt':'y=a*x'}))
            with patch('agent.Client',FakeClient),contextlib.redirect_stdout(io.StringIO()):
                code=agent.run(argparse.Namespace(input=str(case),output=str(out),model='test'))
            self.assertEqual(code,0)
            trace=[json.loads(x) for x in (out/'trace.jsonl').read_text().splitlines()]
            self.assertEqual(trace[-1]['result']['requests'],3)
            self.assertTrue(any(e['action']=='correction_required' for e in trace))
            self.assertTrue((out/'index.html').exists())

    def test_conditionally_meaningful_control_is_not_rejected(self):
        s=fixture()
        s['controls'].append({'id':'flip','type':'toggle','label':'Flip negative inputs',
                             'help':'Changes the sign only for negative x','value':False})
        s['compute']=s['compute'].replace('s.a*s.x','s.a*s.x*(s.flip && s.x<0 ? -1 : 1)')
        checks=validate(s)
        self.assertTrue(next(x for x in checks if x['name']=='control:flip')['passed'])

    def test_wrong_reference_fails(self):
        s=fixture();s['tests'][0]['expect'][0]['value']=7
        self.assertTrue(any(not x['passed'] and x['name']=='known product' for x in validate(s)))

    def test_cosmetic_control_fails(self):
        s=fixture();s['compute']=s['compute'].replace('s.a*s.x','2*s.x')
        r=validate(s)
        self.assertFalse(next(x for x in r if x['name']=='control:a')['passed'])

    def test_nonfinite_is_not_silently_null(self):
        s=fixture();s['compute']='function compute(s){return {x:0/0};}'
        with self.assertRaises(Exception):Engine(s).run()

    def test_infinite_loop_is_bounded(self):
        s=fixture();s['compute']='function compute(s){while(true){};}'
        start=time.monotonic()
        with self.assertRaises(Exception):Engine(s).run()
        self.assertLess(time.monotonic()-start,2)

    def test_input_mutation_rejected(self):
        s=fixture();s['compute']='function compute(s){s.x=10;return {x:1};}'
        with self.assertRaises(Exception):Engine(s).run()

    def test_source_input_aliases_and_missing_excerpt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'case.json'
            base={'source_url':'https://example.org/paper','focus':'product','audience':'student'}
            p.write_text(json.dumps(base))
            with self.assertRaisesRegex(ValueError,'excerpt'):load_case(p)
            for key in ('excerpt','source_excerpt','paper_text'):
                p.write_text(json.dumps({**base,key:'The source says y = a x.'}))
                c,e=load_case(p)
                self.assertEqual(e['excerpt_field'],key)
                self.assertEqual(c['excerpt'],'The source says y = a x.')

    def test_html_escape_preserves_data_and_single_file(self):
        s=fixture();s['intro']='</script><script>alert(1)</script>'
        page=render(s,{'source_url':'https://example.org','excerpt':'<test>'},[])
        self.assertNotIn(s['intro'],page)
        self.assertIn('\\u003c/script',page)
        self.assertNotIn('<script src=',page)
        self.assertIn("connect-src 'none'",page)

    def test_malformed_json_rejected(self):
        self.assertEqual(parse_object('```json\n{"ok":1}\n```'),{'ok':1})
        with self.assertRaises(ValueError):parse_object('not JSON')

    def test_model_and_usage_accounting(self):
        events=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'id':'test-response','model':'exact-model',
                'choices':[{'finish_reason':'stop','message':{'content':'{"ok":1}',
                           'reasoning':'DO NOT LOG THIS'}}],
                'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130,
                         'completion_tokens_details':{'reasoning_tokens':10}}}).encode()
        with patch.dict('os.environ',{'OPENROUTER_API_KEY':'test-secret'}),patch('urllib.request.urlopen',return_value=Response()) as request:
            c=Client('exact-model',lambda *x:events.append(x),time.monotonic())
            self.assertEqual(c.ask('test','system','user'),{'ok':1})
            body=json.loads(request.call_args.args[0].data)
            self.assertEqual(body['model'],'exact-model')
            self.assertEqual(c.completion_tokens,30)
            self.assertEqual(c.prompt_tokens,100)
            self.assertNotIn('DO NOT LOG THIS',str(events))
            self.assertNotIn('test-secret',str(events))
            c.calls=9
            with self.assertRaises(BudgetError):c.ask('test','s','u')


if __name__=='__main__':unittest.main()
