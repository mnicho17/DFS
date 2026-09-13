import copy
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from entry_review import review_entries
from test_portfolio_insights import _lineup


def classic():
    return _lineup(source='optimizer', archetype='', edge=80, duplicate=20, hits={1})


class EntryReviewTests(unittest.TestCase):
    def test_counts_full_bank_without_mutation(self):
        entries=[classic() for _ in range(450)]
        before=copy.deepcopy(entries)
        r=review_entries(entries)
        self.assertEqual(r['valid'],450)
        self.assertEqual(len(r['tables']['Pairs']),36)
        self.assertEqual(len(r['tables']['Trios']),84)
        self.assertEqual(r['tables']['Pairs'][0]['count'],450)
        self.assertEqual(r['tables']['Repeated rosters'][0]['pct'],100)
        self.assertEqual(entries,before)
        self.assertTrue(any(x['label']=='QB + 3 same-team WR/TE' for x in r['tables']['Constructions']))

    def test_captain_identity_and_unknown_metadata(self):
        p=list(classic())[:6]
        for x in p:x['CptSalary']=x['FlexSalary']*1.5
        a={'Captain':p[0],'Flex':p[1:]}
        b={'Captain':p[1],'Flex':[p[0]]+p[2:]}
        r=review_entries([a,a,b], 'showdown')
        self.assertEqual(len(r['tables']['Repeated rosters']),1)
        self.assertEqual(r['tables']['Repeated rosters'][0]['count'],2)
        self.assertEqual(r['tables']['Pairs'][0]['count'],3)
        self.assertEqual(len(r['tables']['Captain + FLEX']),5)
        p[0].pop('Team');p[0].pop('CptSalary')
        r=review_entries([a,a], 'showdown')
        self.assertEqual(r['unknown_context'],2)
        self.assertEqual(r['unknown_salary'],2)
        self.assertIsNone(r['salary_mean'])
        self.assertEqual(len(r['tables']['Captain + FLEX']),5)

    def test_invalid_rosters_and_single_entry(self):
        a=classic();bad=list(a);bad[-1]=bad[0]
        r=review_entries([a,bad,[]])
        self.assertEqual((r['valid'],r['excluded']),(1,2))
        self.assertEqual(r['tables']['Pairs'],[])
        self.assertEqual(review_entries([])['valid'],0)
        a[0].pop('Opponent');a[0].pop('GameKey')
        r=review_entries([a])
        self.assertTrue(any('unknown game' in x['label'] for x in r['tables']['Constructions']))

    def test_dialog_sources_clipboard_and_numeric_sort(self):
        from PyQt5 import QtCore,QtWidgets
        from entry_review_ui import EntryReviewDialog,NumberItem
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        d=EntryReviewDialog([classic()]*450,[classic()]*20,'classic',50000)
        self.assertEqual(d.report['valid'],450)
        d.source.setCurrentIndex(1)
        self.assertEqual(d.report['valid'],20)
        d.findChild(QtWidgets.QPushButton,'copyEntryReview').click()
        self.assertEqual(app.clipboard().text(),d.report['text'])
        self.assertTrue(NumberItem(9,'9')<NumberItem(100,'100'))
        self.assertEqual(d.tabs.widget(1).editTriggers(),QtWidgets.QAbstractItemView.NoEditTriggers)
        d.close()


if __name__=='__main__':unittest.main()
