# saas-billing

Plans and payments module: the team billed customers by sending invoices manually each month.

## What it does

- Stripe Checkout and Billing with three plans, trials and proration
- Webhook handler storing processed event ids so retries never double-apply
- Billing page with invoices, card updates and cancellation

## Results

- manual invoicing: eliminated for 1,200 customers
- duplicate webhook side effects after launch: 0

_Synthetic repository for the GigBid project._
