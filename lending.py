"""
Git repo: 
Bitcoin tips: 

Requires Python 3 and the requests library:
    https://pypi.python.org/pypi/requests/
    
Run it with:
    python3 lending.py
"""
from decimal import Decimal
from datetime import datetime, timedelta
import config
from BFClient import BitfinexAPI

# API key stuff
BITFINEX_API_KEY = config.BITFINEX_API_KEY
BITFINEX_API_SECRET = config.BITFINEX_API_SECRET

# Set this to False if you don't want the bot to make USD offers
LEND_USD = config.LEND_USD

# Set this to False if you don't want the bot to make BTC offers
LEND_BTC = config.LEND_BTC

# Rate to start our USD offers at, in percentage per year
USD_START_RATE_PERCENT = config.USD_START_RATE_PERCENT

# Don't reduce our USD offers below this rate, in percentage per year
USD_MINIMUM_RATE_PERCENT = config.USD_MINIMUM_RATE_PERCENT

# How often to reduce the rates on our unfilled USD offers
USD_RATE_REDUCTION_INTERVAL = config.USD_RATE_REDUCTION_INTERVAL

# How much to reduce the rates on our unfilled USD offers, in percentage per
# year
USD_RATE_DECREMENT_PERCENT = config.USD_RATE_DECREMENT_PERCENT

# ADVANCED: Use this to reduce interest rates exponentially instead of
# linearly. If you don't understand what this means, don't change this
# parameter (leave it at "1.0"). Interest rates will decay towards the minimum
# value rather than towards zero:
#
#   new_rate = (current_rate - min_rate) * multiplier + min_rate
#
# If you use this, you should set USD_RATE_DECREMENT_PERCENT to zero, otherwise
# both reductions will be applied.
USD_RATE_EXPONENTIAL_DECAY_MULTIPLIER = config.USD_RATE_EXPONENTIAL_DECAY_MULTIPLIER

# How many days we're willing to lend our USD funds for
USD_LEND_PERIOD_DAYS = config.USD_LEND_PERIOD_DAYS

# Don't try to make USD offers smaller than this. Bitfinex currently doesn't
# allow loan offers smaller than $50.
USD_MINIMUM_LEND_AMOUNT = config.USD_MINIMUM_LEND_AMOUNT

# Rate to start our BTC offers at, in percentage per year
BTC_START_RATE_PERCENT = config.BTC_START_RATE_PERCENT

# Don't reduce our BTC offers below this rate, in percentage per year
BTC_MINIMUM_RATE_PERCENT = config.BTC_MINIMUM_RATE_PERCENT

# How often to reduce the rates on our unfilled BTC offers
BTC_RATE_REDUCTION_INTERVAL = config.BTC_RATE_REDUCTION_INTERVAL

# How much to reduce the rates on our unfilled BTC offers, in percentage per
# year
BTC_RATE_DECREMENT_PERCENT = config.BTC_RATE_DECREMENT_PERCENT

# ADVANCED: Use this to reduce interest rates exponentially instead of
# linearly. If you don't understand what this means, don't change this
# parameter (leave it at "1.0"). Interest rates will decay towards the minimum
# value rather than towards zero:
#
#   new_rate = (current_rate - min_rate) * multiplier + min_rate
#
# If you use this, you should set BTC_RATE_DECREMENT_PERCENT to zero, otherwise
# both reductions will be applied.
BTC_RATE_EXPONENTIAL_DECAY_MULTIPLIER = config.BTC_RATE_EXPONENTIAL_DECAY_MULTIPLIER

# How many days we're willing to lend our BTC funds for
BTC_LEND_PERIOD_DAYS = config.BTC_LEND_PERIOD_DAYS

# Don't try to make BTC offers smaller than this. Bitfinex currently doesn't
# allow loan offers smaller than $50.
BTC_MINIMUM_LEND_AMOUNT = config.BTC_MINIMUM_LEND_AMOUNT

# How often to retrieve the current statuses of our offers
POLL_INTERVAL = config.POLL_INTERVAL


import time
from collections import defaultdict, deque


def adjust_offers(api, offers, lend_period, minimum_amount):
    """
    Check the specified offers and adjust them as needed.

    Args:
        api: Instance of BitfinexAPI to use.
        offers: Current offers to be adjusted.
        lend_period: How long we're willing to lend our funds for.
        minimum_amount: Make sure any new offers are this amount or higher.

    """
    new_offer_amounts = defaultdict(Decimal)
    if not offers:
        return
    currency = offers[0].currency
    for offer in offers:
        new_rate = offer.get_new_rate()
        if new_rate is not None:
            cancelled_offer = api.cancel_offer(offer)
            new_offer_amounts[new_rate] += cancelled_offer.amount
    for rate, amount in new_offer_amounts.items():
        # The minimum loan amount can cause some weirdness here. If one of our
        # offers gets partially filled and the remainder is below the minimum,
        # we won't be able to place it at the new rate after cancelling. It'll
        # end up with the rest of our funds which get lent out at our starting
        # (highest) rate. The alternative would be to leave small partially
        # filled offers alone, which would mean they no longer get moved down.
        if amount > minimum_amount:
            print(api.new_offer(currency, amount, rate, lend_period))
        else:
            print("At rate {}, {} offer amount {} is below minimum,"
                  " skipping".format(rate, currency, amount))


def go():
    """
    Main loop.

    """
    api = BitfinexAPI(BITFINEX_API_KEY, BITFINEX_API_SECRET)
    print("Ctrl+C to quit")
    while True:
        start_time = datetime.utcnow()

        usd_offers, btc_offers = api.get_offers()
        print(usd_offers)
        print(btc_offers)
        if LEND_USD:
            adjust_offers(api, usd_offers, USD_LEND_PERIOD_DAYS,
                          USD_MINIMUM_LEND_AMOUNT)
        if LEND_BTC:
            adjust_offers(api, btc_offers, BTC_LEND_PERIOD_DAYS,
                          BTC_MINIMUM_LEND_AMOUNT)

        usd_available, btc_available = api.get_available_balances()
        if LEND_USD and usd_available >= USD_MINIMUM_LEND_AMOUNT:
            print(api.new_offer("USD", usd_available, USD_START_RATE_PERCENT,
                                USD_LEND_PERIOD_DAYS))
        if LEND_BTC and btc_available >= BTC_MINIMUM_LEND_AMOUNT:
            print(api.new_offer("BTC", btc_available, BTC_START_RATE_PERCENT,
                                BTC_LEND_PERIOD_DAYS))

        end_time = datetime.utcnow()
        elapsed = end_time - start_time
        remaining = POLL_INTERVAL - elapsed
        delay = max(remaining.total_seconds(), 0)
        print("Done processing, sleeping for", delay, "seconds")
        time.sleep(delay)

def main():
    api = BitfinexAPI(BITFINEX_API_KEY, BITFINEX_API_SECRET)
    lendbook = api.get_lendbook()
    bids = lendbook['bids']
    for bid in list(bids):
        if bid['frr'] == 'Yes':
            bids.pop()
    for bid in bids:
        if Decimal(bid['rate']) >= config.USD_MINIMUM_RATE_PERCENT:
            print(bid)
    print(api.get_available_balances())
if __name__ == "__main__":
    main()


# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# For more information, please refer to <http://unlicense.org/>
