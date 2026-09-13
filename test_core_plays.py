import copy
import os
import unittest
from datetime import datetime,timezone
from core_plays import build_core_plays,format_core_report,CATEGORIES,POSITIVE

NOW=datetime(2026,9,13,12,tzinfo=timezone.utc)


def player(name,projection=12,salary=5000,own=12,**extra):
    p=dict(Name=name,FlexID=name,Position='WR',Team=name,FlexSalary=salary,CptSalary=salary*1.5,
           FlexProjection=projection,CptProjection=projection*1.5,ProjectionSource='Imported forecast',
           ProjOwnPct=own,ProjCptOwnPct=own/2,ProjFlexOwnPct=own,OwnershipUnits='percent_of_entries',
           OwnershipSource='Test estimate',NFLDepthOrder=1,NFLUsageGames=8,
           LiveStatusUpdatedAt='2026-09-13T11:00:00Z')
    p.update(extra);return p


class CorePlaysTests(unittest.TestCase):
    def test_relative_value_anchors_alternatives_and_no_mutation(self):
        pool=[player('Value',18,3000,4),player('Popular',20,7000,25),player('Middle',12),player('Low',6)]
        before=copy.deepcopy(pool);r=build_core_plays(pool,now=NOW);rows={x['name']:x for x in r['rows']}
        self.assertIn(CATEGORIES[0],rows['Value']['categories'])
        self.assertIn(CATEGORIES[3],rows['Value']['categories'])
        self.assertIn(CATEGORIES[1],rows['Popular']['categories'])
        self.assertIn(CATEGORIES[2],rows['Popular']['categories'])
        self.assertNotIn(CATEGORIES[0],rows['Low']['categories'])
        self.assertEqual(rows['Value']['value'],6)
        self.assertEqual(pool,before)
        self.assertIn('Similar mean projection',format_core_report(r))

    def test_showdown_separate_prices_ownership_fades_and_zero(self):
        p=player('One',18,4000,30,ProjCptOwnPct=0,FadeCpt=True)
        rows=build_core_plays([p],'showdown',NOW)['rows'];by={r['slot']:r for r in rows}
        self.assertEqual(by['Captain']['salary'],6000)
        self.assertEqual(by['Captain']['projection'],27)
        self.assertEqual(by['Captain']['ownership'],0)
        self.assertFalse(by['Captain']['eligible']);self.assertTrue(by['FLEX']['eligible'])
        self.assertFalse(POSITIVE.intersection(by['Captain']['categories']))
        self.assertEqual(by['Captain']['value'],by['FLEX']['value'])

    def test_unknown_units_missing_projection_and_ties(self):
        pool=[player(str(i)) for i in range(4)]
        r=build_core_plays(pool,now=NOW)
        self.assertTrue(all(CATEGORIES[0] not in x['categories'] and CATEGORIES[1] not in x['categories'] for x in r['rows']))
        pool=[player('Missing',ProjectionSource='Missing forecast'),player('Zero',0),
              player('Unknown',OwnershipUnits='unknown',ProjOwnPct=.2),player('Bad',FlexSalary=float('nan'))]
        rows={x['name']:x for x in build_core_plays(pool,now=NOW)['rows']}
        self.assertIsNone(rows['Missing']['projection']);self.assertEqual(rows['Zero']['projection'],0)
        self.assertIsNone(rows['Unknown']['ownership']);self.assertFalse(rows['Bad']['eligible'])
        for name in ('Missing','Zero','Bad'):self.assertFalse(POSITIVE.intersection(rows[name]['categories']))

    def test_qb_confirmation_role_changes_staleness_and_partners(self):
        qb=player('QB1',20,7000,20,Team='SEA',Position='QB',InjuryStatus='Q')
        backup=player('QB2',30,3000,40,Team='SEA',Position='QB',NFLDepthOrder=2)
        wr=player('Receiver',Team='SEA')
        rows={x['name']:x for x in build_core_plays([qb,backup,wr],now=NOW)['rows']}
        self.assertFalse(rows['QB2']['eligible']);self.assertFalse(rows['QB2']['promoted'])
        self.assertIn('Receiver',rows['QB1']['partners'][0])
        qb['InjuryStatus']='OUT';backup['LiveStatusUpdatedAt']='2026-09-10T11:00:00Z'
        rows={x['name']:x for x in build_core_plays([qb,backup,wr],now=NOW)['rows']}
        self.assertTrue(rows['QB2']['eligible']);self.assertIn(CATEGORIES[4],rows['QB2']['categories'])
        self.assertTrue(any('24 hours' in c for c in rows['QB2']['concerns']))
        self.assertFalse(rows['QB1']['eligible'])
        missing=build_core_plays([backup],now=NOW)['rows'][0]
        self.assertFalse(missing['eligible'])

    def test_rookie_role_unknown_depth_and_small_groups(self):
        pool=[player('Rookie',Rookie=True,HistoricalPPG=0),player('Unknown',NFLDepthOrder=0)]
        rows={x['name']:x for x in build_core_plays(pool,now=NOW)['rows']}
        self.assertTrue(rows['Rookie']['eligible']);self.assertIn(CATEGORIES[5],rows['Rookie']['categories'])
        self.assertIn('Explicit rookie flag',rows['Rookie']['concerns'])
        self.assertFalse(POSITIVE.intersection(rows['Unknown']['categories']))
        self.assertIsNone(rows['Rookie']['value_percentile'])
        self.assertEqual(build_core_plays([],now=NOW)['rows'],[])

    def test_ui_filters_clipboard_numeric_unknown_last_and_read_only(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5 import QtCore,QtWidgets
        from core_plays_ui import CorePlaysDialog
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        pool=[player('Ten',10,10000,10),player('Two',20,2000,2),player('Unknown',OwnershipUnits='unknown')]
        before=copy.deepcopy(pool);d=CorePlaysDialog(pool,'showdown')
        d.category.setCurrentText('All loaded');d.slot.setCurrentText('FLEX')
        self.assertEqual(d.table.rowCount(),3)
        for order in (QtCore.Qt.AscendingOrder,QtCore.Qt.DescendingOrder):
            d.table.sortItems(5,order)
            self.assertEqual(d.ordered_rows()[-1]['name'],'Unknown')
        d.table.sortItems(2,QtCore.Qt.AscendingOrder)
        self.assertEqual(d.ordered_rows()[0]['salary'],2000)
        d.position.setCurrentText('QB');self.assertEqual(d.table.rowCount(),0)
        d.position.setCurrentText('WR')
        d.search.setText('Two');d.copy.click()
        text=app.clipboard().text();self.assertIn('Two [',text);self.assertNotIn('Ten [',text)
        self.assertEqual(d.table.editTriggers(),QtWidgets.QAbstractItemView.NoEditTriggers)
        self.assertEqual(pool,before);d.close()


if __name__=='__main__':unittest.main()
