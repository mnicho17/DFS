"""Cached proposal metadata must preserve labels, arithmetic and input isolation."""
from test_environment import install
install()
import copy
import itertools
import unittest
from unittest.mock import patch
import historical_field as history
from optimizers import _salary,_cpt_salary
from test_field_history_validation import priors
from test_showdown_performance import _showdown_players


class HistoricalMetadataTests(unittest.TestCase):
    def test_every_captain_flex_order_preserves_public_labels_and_salary_boundaries(self):
        players=_showdown_players()[:6]
        for i,p in enumerate(players):
            p.update(Team='A' if i<3 else 'B',Position=('QB','RB','WR','TE','K','DST')[i],FlexSalary=1000+i*.125,CptSalary=1500+i*.0625)
        before=copy.deepcopy(players);metadata=history._ProposalMetadata(players)
        for captain in range(6):
            for flex in itertools.permutations([i for i in range(6) if i!=captain]):
                entry=dict(Captain=players[captain],Flex=[players[i] for i in flex])
                total=sum(_salary(p) for p in entry['Flex'])
                for left in (-.125,0,200,200.125,700,700.125,1200,1200.125):
                    cap=_cpt_salary(players[captain])+total+left
                    self.assertEqual(metadata.labels(captain,flex,total,cap),history.labels(entry,cap))
        self.assertEqual(players,before)

    def test_large_and_small_fractional_salaries_keep_ordered_subtraction(self):
        players=_showdown_players()[:6]
        for p,price in zip(players,(.1,1e16,.1,.2,.3,1.)):
            p.update(FlexSalary=price,CptSalary=price*1.5,Team='A' if p is players[0] else 'B')
        metadata=history._ProposalMetadata(players)
        for flex in itertools.permutations(range(1,6)):
            total=sum(metadata.salaries[i] for i in flex)
            entry=dict(Captain=players[0],Flex=[players[i] for i in flex])
            for cap in (1e16,1e16+200,1e16+700,1e16+1200):
                self.assertEqual(metadata.labels(0,flex,total,cap),history.labels(entry,cap))

    def test_metadata_is_local_and_one_team_labels_keep_original_shape(self):
        first=_showdown_players()[:6];second=copy.deepcopy(first)
        for p in second:p.update(Position='K',Team='X',FlexSalary=1,CptSalary=2)
        for pool in (first,second,first):
            metadata=history._ProposalMetadata(pool);flex=tuple(range(1,6))
            self.assertEqual(metadata.labels(0,flex,sum(metadata.salaries[i] for i in flex),50000),history.labels(dict(Captain=pool[0],Flex=pool[1:])))

    def test_salary_lookups_scale_with_pool_and_returned_field_not_proposal_attempts(self):
        players=_showdown_players();count=50
        with patch('historical_field._salary',wraps=_salary) as salary,patch('historical_field._cpt_salary',wraps=_cpt_salary) as captain:
            result=history.sample_history_field(players,count,priors(),seed=17)
        self.assertEqual(len(result),count)
        self.assertGreater(result.diagnostic['attempts'],1000)
        self.assertEqual(salary.call_count,3*len(players)+5*count)
        self.assertEqual(captain.call_count,4*len(players)+count)
