# Where to learn this properly

Organised by the problem it solves for this project, not by topic, because a
reading list sorted by subject is a list nobody opens. Each entry says what it
is for here.

Two things to know before any of it.

**There is no academic literature on NFL player prop markets.** I looked. The
published work on NFL betting is almost all point spreads and totals. There is
nothing to copy for the thing this project actually does, which is both the
opportunity and the warning: an easy edge in a liquid market tends to get
written up and then arbitraged away.

**Most published NFL prediction accuracy is leakage.** Of six papers I was given,
three predict a game using that same game's box score and report 83 to 85%
accuracy, one of them peer-reviewed in Elsevier's *Decision Analytics Journal*
and admitting it in its own future-work section. A fourth reports MAE 13 on
passing yards, which is not achievable out of sample. Our honest numbers are on a
harder problem. Do not use theirs as a target. See
`docs/MODEL.md` and the notes in `services/training/research_*.py`.

---

## 1. Combining our projection with the line

The single most useful idea I have found for this project, and the one with the
deepest literature behind it. nfelo's post
[Using Market Regression to Improve Prediction Accuracy in the NFL](https://www.nfeloapp.com/analysis/using-market-regression-to-improve-prediction-accuracy-in-the-nfl/)
is the practical version: blend the model with the market, weight the blend by
recent relative accuracy, and note that a model whose best weight on the market
is 100% has no signal at all. `research_market_regression.py` applies that test
here and puts our own weight at 5 to 35% depending on the market.

The academic name for it is **forecast combination**, and the results are
consistent across fifty years:

- **Bates, J. M. and Granger, C. W. J. (1969), "The Combination of Forecasts",
  *Operational Research Quarterly* 20(4), 451-468.** The origin. Combining two
  forecasts beats both when their errors are not perfectly correlated, which is
  almost always.
- **Clemen, R. T. (1989), "Combining forecasts: A review and annotated
  bibliography", *International Journal of Forecasting* 5(4), 559-583.** The
  survey. Its conclusion is that simple averages are remarkably hard to beat with
  cleverer weighting, which is worth remembering before fitting a weight per
  team per week.
- **Timmermann, A. (2006), "Forecast Combinations", in *Handbook of Economic
  Forecasting* Vol. 1.** The theory of why combination works and when optimal
  weights are worse than equal ones because the weights themselves are estimated.

Read in that order. The third one is the reason to be suspicious of nfelo's
error-weighted refinement before testing it.

## 2. Why the median beats the mean at picking sides

We measured this and wrote it down as a curiosity. It is a theorem.

- **Gneiting, T. (2011), "Quantiles as optimal point forecasts", *International
  Journal of Forecasting* 27(2), 197-207.** The optimal point forecast depends
  entirely on the loss function being used to judge it. Under absolute loss the
  median is optimal; under squared loss the mean is. A board judged on which side
  of a line it lands should be forecasting a quantile, not a mean. This is the
  theoretical statement of our "the low median is the edge" finding and it says
  the finding is not luck.
- **Koenker, R. and Bassett, G. (1978), "Regression Quantiles", *Econometrica*
  46(1), 33-50.** The method our quantile models use.
- **Koenker, R. (2005), *Quantile Regression*, Cambridge University Press.** The
  book, for when the quantile ladder misbehaves at the tails, which ours does.

## 3. Probability calibration, which is where our edge actually lives

- **Gneiting, T. and Raftery, A. E. (2007), "Strictly Proper Scoring Rules,
  Prediction, and Estimation", *JASA* 102(477), 359-378.**
  [PDF](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf).
  Why log loss and Brier are the honest ways to score a probability and accuracy
  is not. Read this before choosing another metric.
- **Brier, G. W. (1950), "Verification of forecasts expressed in terms of
  probability", *Monthly Weather Review* 78(1), 1-3.** Three pages. The Brier
  score decomposes into calibration plus refinement, which is exactly the
  distinction between our model ranking well and claiming too much.
- **Niculescu-Mizil, A. and Caruana, R. (2005), "Predicting Good Probabilities
  With Supervised Learning", *ICML*.** Why boosted trees are systematically
  over-confident at the extremes and why isotonic regression fixes it better than
  a sigmoid. The reference Mecha's xScore leans on, and the justification for what
  `fit_probability_calibrator.py` does.
- **Zadrozny, B. and Elkan, C. (2002), "Transforming classifier scores into
  accurate multiclass probability estimates", *KDD*.** Isotonic calibration.
- **Guo, C. et al. (2017), "On Calibration of Modern Neural Networks", *ICML*.**
  Temperature scaling, and the clearest modern treatment of reliability diagrams
  and expected calibration error. ECE is a number we should be tracking and are
  not.

## 4. Thin samples, which is our whole September problem

The week-2 and week-3 board is a small-sample estimation problem wearing a
football costume, and there is a canonical treatment.

- **Efron, B. and Morris, C. (1975), "Data Analysis Using Stein's Estimator and
  its Generalizations", *JASA* 70(350), 311-319.** The famous one, on 1970
  baseball batting averages: shrinking each player's early-season rate toward the
  league mean predicts the rest of the season better than his own average does,
  for every single player. That is precisely the situation this project was in
  when a two-game window was read as a role.
- **Gelman, A. and Hill, J. (2007), *Data Analysis Using Regression and
  Multilevel/Hierarchical Models*, Cambridge.** Partial pooling done properly.
  A hierarchical model with a player effect nested in a position effect is the
  principled version of the `ewma_shrunk` feature, and would give us shrinkage
  that adapts per player instead of a fixed `n/(n+2)`.
- **Gelman, A. et al. (2013), *Bayesian Data Analysis*, 3rd ed.** The reference
  behind the above. Chapter 5 on hierarchical models is the relevant part.

## 5. Betting market efficiency and known biases

Where the mispricings are documented, which is where to look before inventing a
new theory.

- **Levitt, S. D. (2004), "Why are gambling markets organised so differently
  from financial markets?", *The Economic Journal* 114(495), 223-246.** The
  foundational insight: a book does not balance its action, it exploits known
  bettor biases, so prices are deliberately shaded rather than efficient. This is
  the mechanism behind our own edge and worth reading first in this section.
- **Shank, C. A. (2018), "Is the NFL betting market still inefficient?",
  *Journal of Economics and Finance*; and Shank (2019) on bettor biases.**
  Finds that bettors prefer the over in totals markets. Independent support for
  the over-shade we measured, from a different market.
- **Borghesi, R. (2007), "The home team weather advantage and biases in the NFL
  betting market", *Journal of Economics and Business* 59(4).** Cold-acclimatised
  home teams underpriced in extreme conditions; also documents spread bias growing
  in the final weeks of a season.
- **Paul, R. J. and Weinbach, A. P., various, and Paul (2017) on atmospheric
  conditions.** Temperature, humidity, precipitation, barometric pressure, wind
  and altitude all affect scoring and the market does not fully adjust. We hold
  temperature, wind, roof and surface. We do not hold humidity, precipitation,
  pressure or altitude. That is a cheap, cited feature gap.
- **[Beating the House: Identifying Inefficiencies in Sports Betting
  Markets](https://arxiv.org/pdf/1910.08858)** and
  **[Bettor biases and market efficiency in the NFL totals
  market](http://www.aabri.com/manuscripts/193138.pdf)** for the current shape of
  the literature.

## 6. Bet sizing, which we do not do at all

The board publishes picks and says nothing about stake. That is a gap with a
well-developed answer.

- **Kelly, J. L. (1956), "A New Interpretation of Information Rate", *Bell System
  Technical Journal*.** The original.
- **MacLean, L. C., Thorp, E. O. and Ziemba, W. T. (2010), "Long-term capital
  growth: the good and bad properties of the Kelly and fractional Kelly capital
  growth criteria", *Quantitative Finance* 10(7), 681-687.** The one that matters
  in practice. Full Kelly assumes you know your edge exactly. We do not, our edge
  estimate is a calibrated probability with real uncertainty, and full Kelly under
  an over-estimated edge ruins the bankroll. Fractional Kelly is the standard
  answer and this is why.
- **[Optimal sports betting strategies in practice: an experimental
  review](https://arxiv.org/abs/2107.08827)** for an empirical comparison.

## 7. General method, in order of how often I would actually open them

- **Hastie, T., Tibshirani, R. and Friedman, J., *The Elements of Statistical
  Learning*, 2nd ed.** Free from Stanford. Chapter 7 on model assessment and
  selection is the part that matters here, and specifically why a validation set
  reused for selection stops being a validation set. This project has made that
  mistake more than once.
- **Hyndman, R. J. and Athanasopoulos, G., *Forecasting: Principles and
  Practice*, 3rd ed.** Free at <https://otexts.com/fpp3/>. The chapter on
  time-series cross-validation is the textbook statement of the rolling-origin
  evaluation we use, and the chapter on judging accuracy explains why a single
  train/test split misled us on the model bakeoff.
- **Lundberg, S. M. and Lee, S.-I. (2017), "A Unified Approach to Interpreting
  Model Predictions", *NeurIPS*.** SHAP. We shipped 81 features on rushing yards
  for months without the player's own position among them. This is the tool that
  finds that class of error in an afternoon.
- **Molnar, C., *Interpretable Machine Learning*.** Free at
  <https://christophm.github.io/interpretable-ml-book/>. Practical companion to
  the above.
- **Kuhn, M. and Johnson, K., *Feature Engineering and Selection*.** Free at
  <http://www.feat.engineering/>. Careful treatment of leakage and of doing
  selection inside resampling rather than before it.

## 8. Journals and venues worth following

- ***Journal of Quantitative Analysis in Sports*** (De Gruyter). The main venue.
  Yurko et al. on nflWAR and nflfastR's expected points came from this world.
- ***Journal of Sports Analytics*** (IOS Press).
- ***International Journal of Forecasting***. Not sports-specific and more
  useful than most sports journals for our actual problems: combination,
  calibration, evaluation.
- ***Journal of Sports Economics*** and ***Journal of Economics and Finance***
  for market efficiency work.
- **MIT Sloan Sports Analytics Conference** proceedings. Uneven, occasionally
  excellent, and free.
- **nflverse** itself: the `nflfastR` expected points and win probability models
  are documented openly and are the closest thing to a reference implementation
  of the data we already use.

## 9. What to actually do with this

In order of expected value for this project:

1. Test the market blend properly, including its effect on how many picks clear
   the EV bar (section 1). Started in `research_market_regression.py`.
2. Report Brier and Brier Skill Score against the book's implied probability as
   the headline "does the model add anything" number (section 3).
3. Game-clustered bootstrap confidence intervals on every research result, so we
   stop acting on 1% differences (section 7, and Mecha's drive-clustered
   bootstrap).
4. SHAP on every market (section 7).
5. A hierarchical shrinkage model for the early-season role problem, replacing
   the fixed `n/(n+2)` (section 4).
6. The four missing weather features (section 5).
7. Fractional Kelly stake sizing on the board (section 6).
