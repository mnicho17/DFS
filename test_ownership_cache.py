from test_environment import install
install()
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from main_window import OwnershipSimWorker
from ownership_cache import context,estimate
from test_showdown_performance import _showdown_players
from test_nfl_logic import _fixture_players


class OwnershipCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        p=patch('ownership_cache.model_id',return_value='model-one');p.start();self.addCleanup(p.stop)

    def worker(self,mode='showdown',**kw):
        return OwnershipSimWorker(_showdown_players() if mode=='showdown' else _fixture_players(),
            mode=mode,num_sims=kw.get('count',20),salary_cap=kw.get('cap',50000),template_sim=kw.get('template',False))

    def run_cache(self,w,simulate=None):return estimate(w,simulate or w._simulate,root=self.root)

    def test_showdown_reuse_is_exact_and_skips_estimator(self):
        w=self.worker();expected=w._simulate();actual,report=self.run_cache(w)
        self.assertEqual(actual,expected);self.assertEqual(report['status'],'refreshed');self.assertTrue(report['stored'])
        result,report=self.run_cache(self.worker(),lambda:self.fail('estimator repeated'))
        self.assertEqual(result,expected);self.assertEqual(report['status'],'reused')

    def test_classic_roundtrip_retains_tuple_pair_keys_and_exact_metrics(self):
        w=self.worker('classic');expected=w._simulate()
        result,report=self.run_cache(w,lambda:expected)
        self.assertTrue(report['stored']);self.assertTrue(expected['meta']['pair_counts'])
        actual,report=self.run_cache(self.worker('classic'),lambda:self.fail('estimator repeated'))
        self.assertEqual(actual,expected);self.assertEqual(report['status'],'reused')
        self.assertTrue(all(isinstance(k,tuple) for k in actual['meta']['pair_counts']))

    def test_changed_inputs_settings_order_and_model_miss(self):
        self.run_cache(self.worker())
        for key,value in [('FlexProjection',99),('FlexSalary',9999),('FlexID','new-id'),
                          ('InjuryStatus','OUT'),('FutureModelInput',{'value':1})]:
            with self.subTest(key=key):
                w=self.worker();w.players[0][key]=value;called=[]
                result,report=self.run_cache(w,lambda:called.append(True) or {})
                self.assertEqual(called,[True]);self.assertEqual(report['status'],'refreshed')
        for w in (self.worker(count=21),self.worker(cap=49000)):
            called=[];self.run_cache(w,lambda:called.append(True) or {});self.assertTrue(called)
        w=self.worker();w.future_model_setting=5;called=[]
        self.run_cache(w,lambda:called.append(True) or {});self.assertTrue(called)
        w=self.worker();w.players.reverse();called=[]
        self.run_cache(w,lambda:called.append(True) or {});self.assertTrue(called)
        with patch('ownership_cache.model_id',return_value='different-model'):
            called=[];self.run_cache(self.worker(),lambda:called.append(True) or {});self.assertTrue(called)

    def test_observation_times_and_showdown_overwritten_outputs_do_not_invalidate(self):
        w=self.worker();w.players[0]['NFLUsageHistory']={'checked_at':'old',2026:{'targets':4}}
        expected,first=self.run_cache(w);self.assertTrue(first['stored'])
        w=self.worker();w.players[0]['NFLUsageHistory']={'checked_at':'new',2026:{'targets':4}}
        for p in w.players:
            p.update(ProjOwnPct=99,ProjCptOwnPct=99,ProjFlexOwnPct=99,OwnershipSource='Quick roster-slot estimate',
                OwnershipUnits='percent_of_entries',NFLUsageCheckedAt='new-time',LiveStatusChanged=True)
        self.assertEqual(expected,w._simulate())
        actual,report=self.run_cache(w,lambda:self.fail('unnecessary simulation'))
        self.assertEqual(actual,expected);self.assertEqual(report['status'],'reused')
        w.players[0]['NFLUsageHistory'][2026]['targets']=5
        called=[];self.run_cache(w,lambda:called.append(True) or {});self.assertTrue(called)

    def test_classic_incoming_ownership_remains_strict(self):
        w=self.worker('classic');before=context(w)
        w.players[0]['ProjOwnPct']=77
        self.assertNotEqual(before,context(w))

    def test_partial_invalid_corrupt_and_missing_results_fall_back(self):
        w=self.worker();result=w._simulate()
        partial=copy.deepcopy(result);partial['meta']['valid_lineups']=19
        self.assertFalse(self.run_cache(w,lambda:partial)[1]['stored'])
        self.assertEqual(list(self.root.glob('*.json')),[])
        self.run_cache(w,lambda:result)
        path=next(self.root.glob('*.json'));path.write_text('{broken',encoding='utf-8')
        called=[];actual,report=self.run_cache(w,lambda:called.append(True) or result)
        self.assertEqual(called,[True]);self.assertEqual(actual,result);self.assertEqual(report['status'],'refreshed')
        path.unlink();called=[];self.run_cache(w,lambda:called.append(True) or result);self.assertTrue(called)

    def test_payload_tampering_and_bad_totals_are_not_reused(self):
        from ownership_cache import pack
        from build_snapshots import fingerprint
        w=self.worker();good=w._simulate();self.run_cache(w,lambda:good)
        path=next(self.root.glob('*.json'));saved=json.loads(path.read_text())
        bad=copy.deepcopy(good);bad['total'][next(iter(bad['total']))]=float('nan')
        before=path.read_bytes()
        actual,report=estimate(w,lambda:bad,reuse=False,root=self.root)
        self.assertFalse(report['stored']);self.assertEqual(path.read_bytes(),before)
        # A structurally encoded but inconsistent sample must fail even with a recomputed digest.
        bad=copy.deepcopy(good);bad['cpt'][next(iter(bad['cpt']))]=0
        if bad['cpt']==good['cpt']:bad['cpt'][next(iter(bad['cpt']))]=99
        saved['result']=pack(bad);saved['result_id']=fingerprint(saved['result'])
        path.write_text(json.dumps(saved),encoding='utf-8')
        called=[];actual,report=self.run_cache(w,lambda:called.append(True) or good)
        self.assertTrue(called);self.assertEqual(actual,good);self.assertEqual(report['status'],'refreshed')

    def test_cancelled_estimates_are_not_stored_or_delivered(self):
        w=self.worker();good=w._simulate()
        def compute():w.cancel();return good
        result,report=self.run_cache(w,compute)
        self.assertEqual(report['status'],'cancelled');self.assertEqual(list(self.root.glob('*.json')),[])
        self.assertEqual(result,good)
        w=self.worker();w.cancel()
        self.assertEqual(self.run_cache(w,lambda:self.fail('cancelled simulation'))[1]['status'],'cancelled')

    def test_cache_io_failure_retains_fresh_result_and_inputs(self):
        w=self.worker();good=w._simulate();before=copy.deepcopy(w.players)
        with patch('ownership_cache.tempfile.mkstemp',side_effect=OSError('Disk unavailable')):
            result,report=self.run_cache(w,lambda:good)
        self.assertEqual(result,good);self.assertFalse(report['stored']);self.assertEqual(w.players,before)

    def test_cache_is_bounded_and_does_not_delete_other_files(self):
        keep=self.root/'user-notes.json';keep.write_text('keep')
        with patch('ownership_cache.MAX_ENTRIES',2):
            for cap in (50000,49000,48000):self.run_cache(self.worker(cap=cap))
        self.assertEqual(len(list(self.root.glob('*.json'))),3)
        self.assertEqual(keep.read_text(),'keep')


    def window(self):
        from PyQt5 import QtWidgets
        from main_window import MainWindow
        self.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        w=MainWindow();w.background_queue.timer.stop();w.idle_backup.timer.stop();w.slate_discovery.timer.stop()
        w.players=_showdown_players();w.combo_sport.setCurrentText('NFL');w.tabs_lineups.setCurrentIndex(0)
        w.edit_sd_cap.setText('50000');self.addCleanup(w.close)
        return w

    def wait(self,predicate):
        import time
        end=time.monotonic()+6
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate())

    def test_background_estimate_reuses_after_desktop_quick_setup_and_applies_after_retirement(self):
        w=self.window()
        background=self.worker();expected,_=self.run_cache(background)
        w.chk_sd_template_sim.setChecked(True)
        w.recalc_ownership_quick()
        with patch('ownership_cache.folder',return_value=self.root),patch.object(OwnershipSimWorker,'_simulate',side_effect=AssertionError('repeated simulation')):
            w._start_ownership_sim(num_sims=20,mode='showdown',cap=50000,reuse_prepared=True)
            self.wait(lambda:w._ownership_run is None)
        self.assertEqual(w.last_ownership_reuse['status'],'reused')
        self.assertIn('Reused prepared ownership',w.lbl_ownership_source.text())
        from optimizers import _pkey
        for p in w.players:self.assertEqual(p['ProjOwnPct'],expected['total'].get(_pkey(p),0))
        with patch.object(OwnershipSimWorker,'_simulate',return_value=expected) as fresh:
            w._start_ownership_sim(num_sims=20,mode='showdown',cap=50000)
            self.wait(lambda:w._ownership_run is None)
        fresh.assert_called_once()
        self.assertIsNone(w.last_ownership_reuse)
        self.assertNotIn('Reused prepared ownership',w.lbl_ownership_source.text())

    def test_cached_completion_is_discarded_after_input_change_or_cancel(self):
        import threading
        from ownership_cache import estimate as original
        for cancel in (False,True):
            with self.subTest(cancel=cancel):
                w=self.window();self.run_cache(self.worker());w.recalc_ownership_quick()
                before=[p['ProjOwnPct'] for p in w.players]
                entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
                def blocked(*args,**kwargs):
                    entered.set();release.wait(4)
                    return original(*args,**kwargs)
                with patch('ownership_cache.folder',return_value=self.root),patch('ownership_cache.estimate',side_effect=blocked):
                    w._start_ownership_sim(num_sims=20,mode='showdown',cap=50000,reuse_prepared=True)
                    self.wait(entered.is_set);run=w._ownership_run
                    if cancel:run.cancel()
                    else:w.players[0]['FlexProjection']+=1
                    release.set();self.wait(lambda:w._ownership_run is None)
                self.assertEqual([p['ProjOwnPct'] for p in w.players],before)
                self.assertNotIn('ownership_reuse',w.last_live_check_summary)
                self.assertEqual(w.tasks.rows[run.task]['state'],'Cancelled' if cancel else 'Discarded')


    def test_boolean_sample_metadata_and_oversized_cache_do_not_qualify(self):
        w=self.worker(count=1);good=w._simulate();bad=copy.deepcopy(good);bad['meta']['valid_lineups']=True
        self.assertFalse(self.run_cache(w,lambda:bad)[1]['stored'])
        self.assertFalse(list(self.root.glob('*.json')))
        self.run_cache(w,lambda:good);path=next(self.root.glob('*.json'));path.write_text('x'*129)
        with patch('ownership_cache.MAX_BYTES',128):
            called=[];actual,report=self.run_cache(w,lambda:called.append(True) or good)
        self.assertTrue(called);self.assertEqual(actual,good);self.assertFalse(report['stored'])


if __name__=='__main__':unittest.main()
