# Tagging evaluation

30 reviews were tagged by hand on the [labelling page](https://krishna-1812.github.io/shopify-checkout-reviews-tracker/label.html) on 2026-11-21, without seeing the tracker's tags, and compared with the tags the tracker stored (30 AI). The sample is stratified by app and star band (see `eval/sample.py`) so it over-represents the hard, negative cases; rating-only reviews are excluded because their tag is a fixed rule.

| | Tracker (as stored) | Keyword rules alone |
|---|---|---|
| Sentiment agrees | **100.0%** (30/30) | 100.0% (30/30) |
| Theme agrees | **86.7%** (26/30) | 76.7% (23/30) |
| Both agree | **86.7%** (26/30) | 76.7% (23/30) |

## Two cases it got wrong

**Razorpay, 4★** ([review](https://apps.shopify.com/reviews/1755203))

> It's a flawless Payment checkout integration which works perfectly with the store, It filters the customer using OTP which helps drastically in reducing the fake orders, especially in india and has many more useful features. Great Work RazorPay Team.

- Me: positive / Checkout & conversion
- Tracker: positive / COD & RTO
- Got the theme COD & RTO instead of Checkout & conversion (AI tag). Its reason: “Filters customers using OTP to reduce fake orders”.

**GoKwik, 5★** ([review](https://apps.shopify.com/reviews/2071694))

> A great experience overall with a helpful team and quick and easy process. Thought improvement room is there, None the less a good system for checkout and login!

- Me: positive / General
- Tracker: positive / Setup & onboarding
- Got the theme Setup & onboarding instead of General (AI tag). Its reason: “Mentions helpful team and quick and easy process”.

## Sentiment confusion (rows = me, columns = tracker)

| me \ tracker | positive | neutral | negative |
|---|---|---|---|
| positive | 16 | 0 | 0 |
| neutral | 0 | 0 | 0 |
| negative | 0 | 0 | 14 |

## Every disagreement

| Review | Stars | Me | Tracker | Rules |
|---|---|---|---|---|
| [A great experience overall with a helpful team and quick and easy process. Thought improve…](https://apps.shopify.com/reviews/2071694) | 5 | positive / general | positive / setup | positive / support |
| [It's a flawless Payment checkout integration which works perfectly with the store, It filt…](https://apps.shopify.com/reviews/1755203) | 4 | positive / conversion | positive / cod_rto | positive / cod_rto |
| [It is a nice App with great payment integration, and Deepak was very cooperative.](https://apps.shopify.com/reviews/1694184) | 5 | positive / general | positive / features | positive / setup |
| [great service and experience.. we are using from past 2 month ...](https://apps.shopify.com/reviews/1632139) | 5 | positive / general | positive / support | positive / general |
