import importlib.util
import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibration_trial as trial


class CalibrationTests(unittest.TestCase):
    def test_identity_and_coherence(self):
        m = trial.score_matrix(1.4,1.1,-.05)
        np.testing.assert_allclose(trial.apply_calibration(m,np.zeros(3)),m,atol=1e-14)
        q = trial.apply_calibration(m,np.array([.2,-.3,.1]))
        mk = trial.goal_markets(q)
        self.assertAlmostEqual(q.sum(),1)
        self.assertTrue(np.all(q>=0))
        self.assertAlmostEqual(mk['1']+mk['X']+mk['2'],1)
        self.assertAlmostEqual(mk['1X'],mk['1']+mk['X'])
        self.assertAlmostEqual(mk['GG']+mk['NG'],1)
        for line in (.5,1.5,2.5,3.5,4.5):
            self.assertAlmostEqual(mk[f'Over {line}']+mk[f'Under {line}'],1)

    def test_recover_bias_on_independent_synthetic_sample(self):
        rng=np.random.default_rng(12)
        base=trial.score_matrix(1.4,1.1,-.05)
        truth=trial.apply_calibration(base,np.array([.4,-.4,.35]))
        samples=rng.choice(base.size,4000,p=truth.ravel())
        train=[divmod(int(x),base.shape[1]) for x in samples[:2000]]
        theta=trial.fit_calibration([base]*2000,train)
        calibrated=trial.apply_calibration(base,theta)
        test=samples[2000:]
        self.assertLess(-np.log(calibrated.ravel()[test]).mean(),-np.log(base.ravel()[test]).mean())

    def test_temporal_overlap_rejected(self):
        with self.assertRaises(ValueError):
            trial.split_records([{'season':'2425','date':'2025-08-02'},
                                 {'season':'2526','date':'2025-08-01'}],'2425','2526')

    def test_real_odds_only_and_metrics_use_supplied_probabilities(self):
        rows=[{'h':1,'a':0,'odds':[None,None,None,None,None]},
              {'h':0,'a':1,'odds':[2.,float('nan'),1.8,None,None]}]
        bad=[[.1,.1,.8,.4,.6],[.8,.1,.1,.4,.6]]
        good=[[.8,.1,.1,.4,.6],[.1,.1,.8,.4,.6]]
        mb,_=trial.metrics(rows,bad)
        mg,_=trial.metrics(rows,good)
        self.assertLess(mg['logloss_1x2'],mb['logloss_1x2'])
        self.assertEqual(mg['real_odds_picks'],1)
        self.assertEqual(mg['coverage'],.5)
        self.assertAlmostEqual(mg['roi_real_odds'],.8)

    def test_full_evaluation_and_test_labels_do_not_change_fit(self):
        rng=np.random.default_rng(21)
        base=trial.score_matrix(1.3,1.1,-.05)
        rows=[]
        for year,season in [(2024,'2425'),(2025,'2526')]:
            for i in range(120):
                h,a=divmod(int(rng.choice(base.size,p=base.ravel())),base.shape[1])
                mk=trial.goal_markets(base)
                rows.append({'season':season,'date':f'{year}-09-{i%28+1:02}',
                             'base':base,'model':base,'h':h,'a':a,
                             'market':[mk[k] for k in trial.KEYS], 'odds':[2.5,3.1,2.8,None,None]})
        result=trial.evaluate(rows,'2425','2526')
        changed=[{**r,'h':0,'a':0} if r['season']=='2526' else r for r in rows]
        other=trial.evaluate(changed,'2425','2526')
        np.testing.assert_allclose(result['theta'],other['theta'])
        self.assertEqual(result['results']['calibrated90']['n'],120)
        self.assertNotEqual(result['results']['calibrated90']['logloss_1x2'],other['results']['calibrated90']['logloss_1x2'])

    def test_legacy_arena_metrics_receive_transformed_rows(self):
        # Extract pure functions without importing optional ML/backtest dependencies.
        import ast
        path=Path(__file__).resolve().parents[1]/'arena.py'
        module=ast.parse(path.read_text())
        names={'recalibrate_markets','evaluate','chunks'}
        ns={'np':np,'build_market_rows':lambda args:args[0].to_dict('records'),
            'picks_for':lambda args:[]}
        exec(compile(ast.Module(body=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(path),'exec'),ns)
        class SerialPool:
            def map(self,fn,items):return list(map(fn,items))
        import pandas as pd
        rows,_=ns['evaluate'](SerialPool(),pd.DataFrame([{'mk':{'1':.5}}]),'test',[None],{'1':(1.,1.)})
        self.assertAlmostEqual(rows[0]['mk']['1'],1/(1+np.exp(-1)))


if __name__=='__main__': unittest.main()
