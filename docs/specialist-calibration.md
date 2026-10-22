# Specialist SIM forecast drift

The shared possession model introduced an unintended lower bound for defense
production. Its defense-strength parameter clipped forecasts below 2.2 points
to the same strength; baseline sacks, takeaways and points-allowed events could
then substantially exceed a low forecast. This affected lineup scoring, including
FLEX defenses even when users excluded defense Captains.

A fixed-seed audit of the reported slate used 5,000 scenarios and the exact saved
player inputs. Cowboys DST had a 1.5-point forecast but a 5.99-point simulated
mean; Buccaneers had a 5.3 forecast and 5.72 mean. The two kicker means were close
to their opportunity forecasts (10.01 versus 9.96, and 9.17 versus 9.15). These
are model-consistency observations, not evidence of historical accuracy.

The default specialist model now retains the existing projection-centered DST
draw, including volatility, game/opponent correlation and the -4 score floor.
Repeating the same 5,000 scenarios after the change produced means of 1.49 for
Cowboys and 5.26 for Buccaneers; kicker outcomes were identical to the baseline.
It does not overwrite DST scores with the uncalibrated possession model.
Kicker opportunity events and offensive draws are unchanged. Team events still
include defensive return PAT opportunities for kickers. Defense and offensive
fantasy points are forecast distributions rather than a reconciled play-by-play
score. No specialist exposure or lineup construction limits are introduced.

Historical validation of kicker tails, defense variance, both-defense lineups
and specialist/offense correlation remains necessary before claiming calibrated
winning probabilities. This change corrects forecast drift rather than fitting
new event rates to the same slate. Code identity invalidates old screening scores;
the structural roster library remains reusable. Older saved reports retain their
original specialist model description.
