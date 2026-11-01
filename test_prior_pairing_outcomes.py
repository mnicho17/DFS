from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
from prior_pairing_outcomes import WithinUser,summarize,render
from portfolio_pattern_audit import PatternAuditLimit


def slots(qb=True,captain='1',salary=7000):
    positions=['WR','QB' if qb else 'RB','WR','RB','TE','WR']
    return [(str(i),'CPT' if i==0 else 'FLEX',dict(id=captain if i==0 else str(i+10),position=p,team='A' if i<3 else 'B',salary=salary)) for i,p in enumerate(positions)]


class PriorPairingOutcomeTests(unittest.TestCase):
    def test_contest_denominator_support_and_rank_ties(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),11)
        v=a.finish(True)['support_checks']['10']['receiver_captain_same_qb']
        self.assertEqual(v['strata'],1);self.assertEqual(v['difference_pp'],dict(top1=100,top5=0,rank1=100))
        self.assertEqual(v['entries'],20)

    def test_exact_captain_salary_and_fixed_support_are_required(self):
        for changed in (slots(False,captain='2'),slots(False,salary=8000)):
            a=WithinUser(1000)
            for _ in range(10):a.add(slots(),1);a.add(changed,20)
            self.assertEqual(a.finish(True)['support_checks']['5']['receiver_captain_same_qb']['strata'],0)
        a=WithinUser(1000)
        for _ in range(5):a.add(slots(),1);a.add(slots(False),20)
        self.assertEqual(a.finish(True)['support_checks']['5']['receiver_captain_same_qb']['strata'],1)
        self.assertEqual(a.finish(True)['support_checks']['10']['receiver_captain_same_qb']['strata'],0)

    def test_unknown_portfolio_and_missing_ranks_do_not_become_losses(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),None)
        self.assertEqual(a.finish(True)['support_checks']['5']['receiver_captain_same_qb']['strata'],0)
        for _ in range(10):a.add(slots(False),20)
        self.assertFalse(a.finish(False)['complete'])
        self.assertEqual(a.finish(False)['support_checks']['5']['receiver_captain_same_qb']['strata'],0)

    def test_cell_bound_aborts_and_invalid_denominators_fail(self):
        with patch('prior_pairing_outcomes.MAX_CELLS',0):
            with self.assertRaises(PatternAuditLimit):WithinUser(100).add(slots(),1)
        for n in (0,True,None):
            with self.assertRaises(ValueError):WithinUser(n)

    def test_legacy_reports_are_unavailable_and_equal_slate_weighting(self):
        r=dict(review_mode='prior_qualified',contests=[])
        self.assertEqual(summarize(r),{});self.assertIn('Unavailable',render(r))
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),20)
        p=dict(pairing_outcomes=a.finish(True))
        c=dict(format='showdown',preset='150-Max',date='2026-10-01',slate=['showdown','2026-10-01',['A@B']],winners=[p]*10)
        other=copy.deepcopy(c);other.update(date='2026-10-02',slate=['showdown','2026-10-02',['C@D']])
        for v in other['winners'][0]['pairing_outcomes']['support_checks']['10'].values():
            if v['strata']:v['difference_pp']={e:0 for e in v['difference_pp']}
        r['contests']=[c,c,other]
        v=summarize(r)['showdown / 150-Max']['receiver_captain_same_qb']
        self.assertEqual(v['slates'],2);self.assertEqual(v['endpoints']['top1']['difference_pp'],50)
        r['review_mode']='winners'
        with self.assertRaises(ValueError):summarize(r)

    def test_fixed_flags_preserve_supported_primary_contrast(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),20)
        v=a.finish(True)
        self.assertEqual(v['support_checks'],v['other_groups_fixed'])

    def test_each_changed_group_flag_cannot_borrow_primary_support(self):
        from showdown_group_review import flags
        for flag in ('kickers','defenses','quarterbacks','qb_dst'):
            with self.subTest(flag=flag):
                row=slots()
                if flag in ('kickers','defenses'):
                    for i in (3,4):row[i][2]['position']='K' if flag=='kickers' else 'DST'
                    if flag=='defenses':row[3][2]['team']=row[4][2]['team']='A'
                elif flag=='quarterbacks':row[3][2]['position']='QB'
                else:row[3][2]['position']='DST'
                self.assertTrue(flags(row)[flag]);self.assertFalse(flags(slots(False))[flag])
                a=WithinUser(1000)
                for _ in range(10):a.add(row,1);a.add(slots(False),20)
                v=a.finish(True)
                self.assertEqual(v['support_checks']['10']['receiver_captain_same_qb']['strata'],1)
                self.assertEqual(v['other_groups_fixed']['10']['receiver_captain_same_qb']['strata'],0)

    def test_conditional_strata_do_not_pool_toward_minimum_support(self):
        a=WithinUser(1000)
        for q in (True,False):
            for double in (True,False):
                row=slots(q)
                if double:
                    for i in (3,4):row[i][2]['position']='K'
                for _ in range(5):a.add(row,1 if q else 20)
        v=a.finish(True)
        self.assertEqual(v['support_checks']['10']['receiver_captain_same_qb']['strata'],1)
        self.assertEqual(v['other_groups_fixed']['10']['receiver_captain_same_qb']['strata'],0)
        self.assertEqual(v['other_groups_fixed']['5']['receiver_captain_same_qb']['strata'],2)
        self.assertTrue(all(not v['strata'] for v in a.finish(False)['other_groups_fixed']['5'].values()))

    def test_old_outcome_reports_cannot_claim_fixed_group_evidence(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),20)
        d=a.finish(True);d.pop('other_groups_fixed')
        r=dict(review_mode='prior_qualified',contests=[dict(format='showdown',preset='150-Max',date='2026-10-01',slate=['showdown','2026-10-01',['A@B']],winners=[dict(pairing_outcomes=d)])])
        self.assertTrue(summarize(r));self.assertEqual(summarize(r,other_groups_fixed=True),{})
        self.assertIn('Unavailable',render(r))

    def test_three_specialists_cannot_match_zero_under_all_fixed_flags(self):
        a=WithinUser(1000);row=slots()
        for i,pos in ((3,'K'),(4,'K'),(5,'DST')):row[i][2]['position']=pos
        for _ in range(10):a.add(row,1);a.add(slots(),20)
        d=a.finish(True)
        self.assertEqual(d['support_checks']['10']['kd_three_plus_vs_zero']['strata'],1)
        self.assertEqual(d['other_groups_fixed']['10']['kd_three_plus_vs_zero']['strata'],0)

    def test_one_qb_comparison_rejects_zero_qb_baseline(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),20)
        d=a.finish(True)
        self.assertEqual(d['other_groups_fixed']['10']['receiver_captain_same_qb']['strata'],1)
        self.assertEqual(d['one_qb_receiver']['5']['receiver_captain_same_qb']['strata'],0)

    def test_one_qb_compares_own_team_against_opposing_team(self):
        a=WithinUser(1000);opposing=slots();opposing[1][2]['team']='B'
        for _ in range(10):a.add(slots(),1);a.add(opposing,20)
        d=a.finish(True)['one_qb_receiver']['10']['receiver_captain_same_qb']
        self.assertEqual(d['strata'],1);self.assertEqual(d['entries'],20)
        self.assertEqual(d['difference_pp']['top1'],100)
        self.assertEqual(a.finish(True)['one_qb_receiver']['10'].keys(),{'receiver_captain_same_qb'})
        self.assertEqual(a.finish(False)['one_qb_receiver']['5']['receiver_captain_same_qb']['strata'],0)

    def test_one_qb_does_not_borrow_other_flags_or_captain_support(self):
        for changed in ('flags','captain','salary','multiple'):
            with self.subTest(changed=changed):
                row=slots();row[1][2]['team']='B'
                if changed=='flags':row[3][2].update(position='DST',team='A')
                if changed=='captain':row[0][2]['id']='different'
                if changed=='salary':
                    for _,_,p in row:p['salary']=8000
                if changed=='multiple':row[3][2].update(position='QB',team='B')
                a=WithinUser(1000)
                for _ in range(10):a.add(slots(),1);a.add(row,20)
                self.assertEqual(a.finish(True)['one_qb_receiver']['5']['receiver_captain_same_qb']['strata'],0)

    def test_one_qb_legacy_evidence_and_support_stay_unavailable(self):
        a=WithinUser(1000);opposing=slots();opposing[1][2]['team']='B'
        for _ in range(5):a.add(slots(),1);a.add(opposing,20)
        d=a.finish(True)
        self.assertEqual(d['one_qb_receiver']['5']['receiver_captain_same_qb']['strata'],1)
        self.assertEqual(d['one_qb_receiver']['10']['receiver_captain_same_qb']['strata'],0)
        r=dict(review_mode='prior_qualified',contests=[dict(format='showdown',preset='150-Max',date='2026-10-01',slate=['showdown','2026-10-01',['A@B']],winners=[dict(pairing_outcomes=d)])])
        self.assertTrue(summarize(r,5,one_qb_receiver=True))
        d.pop('one_qb_receiver');self.assertEqual(summarize(r,5,one_qb_receiver=True),{})
        self.assertIn('Exactly one QB',render(r))
        with self.assertRaises(ValueError):summarize(r,other_groups_fixed=True,one_qb_receiver=True)

    def support_report(self,slates=5,users=10):
        a=WithinUser(1000);opposing=slots();opposing[1][2]['team']='B'
        for _ in range(10):a.add(slots(),1);a.add(opposing,20)
        contests=[]
        for i in range(slates):
            date=f'2026-10-{i+1:02}'
            profiles=[dict(user_key='dk:u'+str(j),pairing_outcomes=copy.deepcopy(a.finish(True))) for j in range(users)]
            contests.append(dict(format='showdown',preset='150-Max',date=date,slate=['showdown',date,['A@B']],winners=profiles))
        return dict(review_mode='prior_qualified',contests=contests)

    def test_strict_support_needs_five_games_and_preserves_default_results(self):
        r=self.support_report();before=copy.deepcopy(summarize(r))
        for mode in ({},{'other_groups_fixed':True},{'one_qb_receiver':True}):
            v=summarize(r,minimum_users_per_slate=10,**mode)['showdown / 150-Max']['receiver_captain_same_qb']
            self.assertEqual(v['evidence_status'],'supported descriptive sample')
            self.assertEqual(v['slates'],5);self.assertEqual(v['excluded_small_slates'],0)
            self.assertTrue(all(g['distinct_users']==10 for g in v['games']))
        self.assertEqual(before,summarize(r))
        r['contests'].pop()
        v=summarize(r,minimum_users_per_slate=10)['showdown / 150-Max']['receiver_captain_same_qb']
        self.assertEqual(v['evidence_status'],'insufficient independent games')
        self.assertIn('independent games 4/5 minimum',render(r))

    def test_same_game_repeat_users_cannot_inflate_distinct_support(self):
        r=self.support_report(slates=1,users=9);r['contests'].append(copy.deepcopy(r['contests'][0]))
        v=summarize(r,minimum_users_per_slate=10)['showdown / 150-Max']['receiver_captain_same_qb']
        self.assertEqual(v['slates'],0);self.assertEqual(v['excluded_small_slates'],1)
        self.assertIsNone(v['endpoints']['top1']['difference_pp'])
        self.assertIn('top1: unavailable',render(r))

    def test_strict_support_is_metric_specific_and_requires_known_users(self):
        r=self.support_report(slates=1)
        r['contests'][0]['winners'][0]['pairing_outcomes']['one_qb_receiver']['10']['receiver_captain_same_qb']['strata']=0
        v=summarize(r,one_qb_receiver=True,minimum_users_per_slate=10)['showdown / 150-Max']['receiver_captain_same_qb']
        self.assertEqual(v['slates'],0)
        for p in r['contests'][0]['winners']:p.pop('user_key')
        self.assertEqual(summarize(r,minimum_users_per_slate=10),{})

    def test_strict_support_does_not_pool_formats_or_entry_presets(self):
        r=self.support_report(slates=1,users=5);other=copy.deepcopy(r['contests'][0]);other['preset']='20-Max'
        r['contests'].append(other)
        for scope in summarize(r,minimum_users_per_slate=10).values():self.assertEqual(scope['receiver_captain_same_qb']['slates'],0)

    def test_invalid_strict_support_and_slate_identity_are_rejected(self):
        r=self.support_report(slates=1)
        for n in (-1,True,1.5):
            with self.assertRaises(ValueError):summarize(r,minimum_users_per_slate=n)
        r['contests'][0]['slate'][1]='2026-09-01'
        with self.assertRaises(ValueError):summarize(r,minimum_users_per_slate=10)

    def test_evidence_summary_flags_thin_games_before_displaying_any_mean(self):
        from prior_pairing_outcomes import render_evidence_summary
        r=self.support_report(slates=2)
        text=render_evidence_summary(r)
        self.assertIn('insufficient independent games',text);self.assertIn('2/5 games minimum',text)
        self.assertNotIn('top-cutoff +100.000',text)
        self.assertIn('structurally unavailable',text)

    def test_evidence_summary_supported_sample_is_descriptive_and_nonmutating(self):
        from prior_pairing_outcomes import render_evidence_summary
        r=self.support_report();before=copy.deepcopy(r)
        text=render_evidence_summary(r)
        self.assertIn('supported descriptive sample',text);self.assertIn('top-cutoff +100.000 pp',text)
        self.assertIn('Sample support does not establish significance',text)
        self.assertEqual(r,before)

    def test_evidence_summary_does_not_invent_missing_legacy_evidence(self):
        from prior_pairing_outcomes import render_evidence_summary
        r=self.support_report()
        for c in r['contests']:
            for p in c['winners']:p['pairing_outcomes'].pop('one_qb_receiver')
        text=render_evidence_summary(r)
        section=text.split('Exactly one QB: own-team versus opposing')[1]
        self.assertIn('unavailable in saved evidence',section)
        self.assertNotIn('supported descriptive sample',section)
        self.assertIn('no evaluated contests',render_evidence_summary(dict(review_mode='prior_qualified',contests=[])))
