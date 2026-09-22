"""RL-02 synthetic storage/Qt boundaries; never uses production history."""
from test_environment import install, network_attempts
install()

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from PyQt5 import QtWidgets
import review_report as rr
import review_build_evidence as be
from review_report_ui import ReviewReportDialog


def fixture(kind='showdown', lock=True):
    names = ['Amber One','Birch Two','Cedar Three','Dawn Four','Ember Five',
             'Fir Six','Grove Seven','Hazel Eight','Ivy Nine']
    roles = ['CPT'] + ['FLEX']*5 if kind == 'showdown' else ['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']
    players = [{'Name':name,'Position':'WR','GameInfo':'AAA@BBB 09/17/2026 08:15PM ET',
                'LockCpt':bool(lock and n==0 and kind=='showdown'),'LockFlex':False,
                'FadeCpt':False,'FadeFlex':False,'FlexProjection':10.0,'MaxCptPct':None}
               for n,name in enumerate(names[:len(roles)])]
    players.append({'Name':'Unused Quarterback','Position':'QB','NFLQBEligible':False,
                    'NFLDepthOrder':2,'GameInfo':players[0]['GameInfo'],'FlexProjection':15.0})
    inputs={'players':players,'recipe':{'sport':'NFL','contest_kind':kind,'requested_lineups':1,
             'balance_ownership':True,'min_unique':2,'deep_compute':{'selection_mode':'Portfolio selection'}},
            'rules':{},'contest':{},'calibration':{}}
    snap={'schema_version':1,'inputs':inputs,'created_at':'2026-09-17T18:00:00-04:00','input_id':be.digest(inputs)}
    rows=[{'slots':[{'slot':slot,'player':dict(p)} for slot,p in zip(roles,players)]}]
    text=' '.join(slot+' '+p['Name'] for slot,p in zip(roles,players))
    return snap,rows,text


class BuildEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rl02-')
        self.root=Path(self.temp.name); self.history=self.root/'history'
        self.history.mkdir(); self.db=self.history/'exports.sqlite'
        self.diag=self.history/'build-diagnostics.json'
        self.dialogs=[]; self.network_before=list(network_attempts)

    def tearDown(self):
        for d in self.dialogs:
            d.close(); self.drain(d)
        self.assertEqual(network_attempts,self.network_before)
        self.temp.cleanup()

    def drain(self,d):
        deadline=time.monotonic()+10
        while d._job and time.monotonic()<deadline:
            self.app.processEvents(); time.sleep(.002)
        self.app.processEvents(); self.assertIsNone(d._job)

    def seed(self, texts, kind='showdown', day='2026-09-17'):
        c=sqlite3.connect(self.db)
        c.execute('CREATE TABLE historical_results(result_id TEXT,sport TEXT,slate_date TEXT,raw_json TEXT)')
        for n,text in enumerate(texts):
            c.execute('INSERT INTO historical_results VALUES (?,?,?,?)',(str(n),'NFL',day,json.dumps({'lineup':text,'contest type':kind})))
        c.commit();c.close()

    def archive(self,snap,rows,name='one.zip', **changes):
        folder=self.history/'build-archives';folder.mkdir(exist_ok=True)
        meta={'schema_version':1,'record_type':'generated_outputs','input_id':snap['input_id'],
              'sport':'NFL','kind':snap['inputs']['recipe']['contest_kind'],'output_count':len(rows),
              'created_at':'2026-09-17T18:10:00-04:00','build_status':'completed','app_code_id':'a'*64, **changes}
        files={'input-snapshot.json':json.dumps(snap).encode(),
               'lineups.json':json.dumps({'metadata':meta,'lineups':rows}).encode(),
               'build-report.txt':b'PRIVATE_RAW_REPORT api_key=FAKE_SECRET_ARCHIVE'}
        manifest={'metadata':meta,'sha256':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
        files['manifest.json']=json.dumps(manifest).encode()
        p=folder/name
        with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:
            for k,v in files.items():z.writestr(k,v)
        return p

    def snapshot(self,snap,name='snapshot.json'):
        folder=self.history/'snapshots';folder.mkdir(exist_ok=True)
        path=folder/name;path.write_text(json.dumps(snap),encoding='utf-8');return path

    def capture(self,options=rr.Options(),**kw):
        return rr.capture(options,db_path=self.db,diagnostic_path=self.diag,**kw)

    def linkage(self,report):return report.data['build_evidence']['result_linkage']['counts']

    def test_exact_archive_lock_and_excluded_forecast_both_formats_read_only(self):
        for kind in ('classic','showdown'):
            with self.subTest(kind=kind):
                snap,rows,text=fixture(kind);self.archive(snap,rows,kind+'.zip')
                if self.db.exists():self.db.unlink()
                self.seed([text],kind)
                before={p:p.read_bytes() for p in self.history.rglob('*') if p.is_file()}
                report=self.capture()
                self.assertEqual(self.linkage(report),{'unique_archive_roster_match':1})
                record=next(r for r in report.data['build_evidence']['records'] if r['format']==kind)
                self.assertEqual(record['player_decisions']['counts']['excluded_qb_positive_projection'],1)
                self.assertIsNone(record['source_revision'])
                self.assertEqual(record['recorded_code_fingerprint'],'a'*64)
                self.assertEqual(record['captain_concentration']['locked_captain_output_count'],int(kind=='showdown'))
                self.assertEqual(report.data['build_evidence']['result_linkage']['certified_original_build_count'],0)
                self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_captain_swap_and_different_date_do_not_match(self):
        snap,rows,text=fixture();self.archive(snap,rows)
        swapped=text.replace('CPT Amber One FLEX Birch Two','CPT Birch Two FLEX Amber One')
        self.seed([swapped])
        self.assertEqual(self.linkage(self.capture()),{'no_supported_evidence':1})
        self.db.unlink();self.seed([text],day='2026-09-18')
        self.assertEqual(self.linkage(self.capture()),{'no_supported_evidence':1})

    def test_multiple_builds_stay_ambiguous_counts_do_not_add(self):
        snap,rows,text=fixture();self.archive(snap,rows);self.archive(snap,rows,'two.zip')
        self.seed([text,text])
        r=self.capture();self.assertEqual(self.linkage(r),{'multiple_archive_roster_matches':2})
        self.assertEqual(r.data['build_evidence']['result_linkage']['target_count'],2)

    def test_snapshot_only_never_claims_output_or_code_revision(self):
        snap,rows,text=fixture();self.snapshot(snap);self.seed([text])
        r=self.capture();self.assertEqual(self.linkage(r),{'snapshot_compatible_only':1})
        record=r.data['build_evidence']['records'][0]
        self.assertEqual(record['source'],'input_snapshot_only')
        self.assertNotIn('output_count',record)

    def test_unknown_eligibility_and_zero_limit_survive(self):
        snap,rows,text=fixture()
        p=snap['inputs']['players'][-1];p.pop('NFLQBEligible');p['MaxCptPct']=0
        snap['input_id']=be.digest(snap['inputs']);self.snapshot(snap);self.seed([text])
        r=self.capture(rr.Options(details=True)).data['build_evidence']['records'][0]
        self.assertEqual(r['player_decisions']['counts']['qb_unknown'],1)
        self.assertEqual(r['player_details'][-1]['limits_pct']['MaxCptPct'],0)
        self.assertIsNone(r['player_details'][-1]['flags']['LockCpt'])

    def test_cancelled_postgame_and_unknown_time_outputs_not_attributed(self):
        snap,rows,text=fixture();self.seed([text])
        for n,changes in enumerate(({'build_status':'cancelled'}, {'created_at':'2026-09-18T01:00:00Z'}, {'created_at':'2026-09-17T18:10:00'})):
            self.archive(snap,rows,str(n)+'.zip',**changes)
        r=self.capture();self.assertEqual(self.linkage(r),{'no_supported_evidence':1})
        self.assertEqual(len(r.data['build_evidence']['records']),3)

    def test_archive_checksum_tamper_and_input_checksum_rejected(self):
        snap,rows,text=fixture();self.seed([text])
        path=self.archive(snap,rows)
        with zipfile.ZipFile(path) as z: files={n:z.read(n) for n in z.namelist()}
        files['lineups.json']=b'{}'
        with zipfile.ZipFile(path,'w') as z:
            for n,v in files.items():z.writestr(n,v)
        snap['inputs']['players'][0]['LockCpt']=False;self.snapshot(snap)
        r=self.capture().data['build_evidence']
        self.assertEqual(r['records'],[]);self.assertEqual(r['issues']['invalid_or_unavailable_file'],2)

    def test_unsupported_zip_members_and_duplicate_names_rejected(self):
        snap,rows,text=fixture();self.seed([text]);path=self.archive(snap,rows)
        with zipfile.ZipFile(path,'a') as z:z.writestr('../escape.txt','not extracted')
        snap['inputs']['players'][1]['Name']=snap['inputs']['players'][0]['Name']
        snap['input_id']=be.digest(snap['inputs']);self.snapshot(snap)
        r=self.capture().data['build_evidence'];self.assertEqual(r['records'],[])
        self.assertFalse((self.root/'escape.txt').exists())

    def test_privacy_defaults_and_opt_in_exact_zip_previews(self):
        snap,rows,text=fixture();self.archive(snap,rows);self.seed([text])
        plain=self.capture();all_text=plain.evidence.decode()+plain.summary()
        for secret in ('Amber One','Unused Quarterback','PRIVATE_RAW_REPORT','FAKE_SECRET_ARCHIVE',str(self.history)):
            self.assertNotIn(secret,all_text)
        d=ReviewReportDialog(db_path=self.db,diagnostic_path=self.diag);self.dialogs.append(d)
        d.detail.setChecked(True);d.generate.click();self.drain(d)
        self.assertIn('Amber One',d.evidence_preview.toPlainText())
        target=self.root/'review.zip'
        with mock.patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(target),'')):
            d.save.click();self.drain(d)
        with zipfile.ZipFile(target) as z:
            self.assertEqual(z.read('evidence.json').decode(),d.evidence_preview.toPlainText())
            self.assertEqual(z.read('summary.md').decode(),d.preview.toPlainText())
            self.assertNotIn('PRIVATE_RAW_REPORT',str(z.read('evidence.json')))

    def test_choose_history_invalidates_frozen_preview_and_reset(self):
        snap,rows,text=fixture();self.archive(snap,rows);self.seed([text])
        d=ReviewReportDialog();self.dialogs.append(d)
        with mock.patch.object(QtWidgets.QFileDialog,'getExistingDirectory',return_value=str(self.history)):
            d.choose_source.click()
        d.generate.click();self.drain(d)
        self.assertEqual(self.linkage(d._report),{'unique_archive_roster_match':1})
        self.assertNotIn(str(self.history),d._report.evidence.decode())
        d.reset_source.click();self.assertIsNone(d._report);self.assertFalse(d.save.isEnabled())
        self.assertIsNone(d.db_path)

    def test_source_folder_publication_protected_including_unread_files(self):
        snap,rows,text=fixture();path=self.archive(snap,rows);self.seed([text])
        report=self.capture();before=path.read_bytes()
        with self.assertRaises(rr.SourceDestination):rr.publish(report,path)
        with self.assertRaises(rr.SourceDestination):rr.publish(report,self.history/'new-output.zip')
        self.assertEqual(before,path.read_bytes())

    def test_byte_file_and_detail_limits_disclosed(self):
        snap,rows,text=fixture();self.snapshot(snap,'a.json');self.snapshot(snap,'b.json');self.seed([text])
        with mock.patch.object(be,'MAX_FILES',1),mock.patch.object(be,'MAX_PLAYER_DETAILS',2):
            r=self.capture(rr.Options(details=True)).data['build_evidence']
        self.assertEqual(r['state'],'partial');self.assertTrue(r['records'][0]['player_details_truncated'])
        self.assertEqual(len(r['records'][0]['player_details']),2)
        with mock.patch.object(be,'MAX_TOTAL_BYTES',1):
            r=self.capture().data['build_evidence']
        self.assertEqual(r['state'],'partial');self.assertIn('byte_limit',r['issues'])

    def test_read_cancellation_and_close_retire_without_publication(self):
        snap,rows,text=fixture();self.archive(snap,rows);self.seed([text])
        stop=[False]
        def progress(message):
            if 'build evidence' in message:stop[0]=True
        with self.assertRaises(rr.Cancelled):self.capture(cancelled=lambda:stop[0],progress=progress)
        d=ReviewReportDialog(db_path=self.db,diagnostic_path=self.diag);self.dialogs.append(d)
        d.generate.click();d.close();self.drain(d)
        self.assertIsNone(d._report)

    def test_missing_invalid_and_filters_do_not_create_history(self):
        r=self.capture();self.assertEqual(r.data['build_evidence']['state'],'missing_or_filtered')
        self.assertFalse(self.db.exists())
        snap,rows,text=fixture();self.archive(snap,rows);self.seed([text])
        r=self.capture(rr.Options(start='2026-09-18',end='2026-09-18')).data['build_evidence']
        self.assertEqual(r['records'],[]);self.assertEqual(r['result_linkage']['target_count'],0)

    def test_latest_equal_time_conflicting_snapshots_remain_ambiguous(self):
        snap,rows,text=fixture();self.snapshot(snap,'one.json')
        other=copy.deepcopy(snap);other['inputs']['players'][0]['LockCpt']=False
        other['input_id']=be.digest(other['inputs']);self.snapshot(other,'two.json');self.seed([text])
        self.assertEqual(self.linkage(self.capture()),{'ambiguous_latest_snapshot':1})

    def test_junction_directories_are_not_followed(self):
        snap,rows,text=fixture();self.archive(snap,rows);self.seed([text])
        with mock.patch.object(Path,'is_junction',return_value=True,create=True):
            r=self.capture().data['build_evidence']
        self.assertEqual(r['records'],[])
        self.assertEqual(r['issues']['unsupported_directory'],2)

    def test_result_identity_exclusions_keep_complete_denominator(self):
        snap,rows,text=fixture();self.archive(snap,rows)
        self.seed([text,'CPT 12345 FLEX 23456','not a roster'])
        r=self.capture().data['build_evidence']
        self.assertEqual(r['result_linkage']['target_count'],3)
        self.assertEqual(r['result_linkage']['counts'],{'unique_archive_roster_match':1,'unqualified_result_identity':2})
        self.assertLessEqual(r['capture_started_at'],r['capture_completed_at'])


if __name__=='__main__':unittest.main()
