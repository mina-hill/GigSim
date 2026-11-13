with orders as (
    select * from {{ ref('stg_shopify__orders') }}
),
refunds as (
    select order_id, sum(amount) as refunded from {{ ref('stg_shopify__refunds') }} group by 1
)
select
    o.order_id,
    o.customer_id,
    o.ordered_at::date as order_date,
    o.gross_amount - coalesce(r.refunded, 0) as net_revenue
from orders o
left join refunds r using (order_id)
