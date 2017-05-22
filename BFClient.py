from decimal import Decimal
from datetime import datetime, timedelta
from itertools import count
import time
import base64
import json
import hmac
import hashlib
import requests
from collections import defaultdict, deque

import config


class Offer(object):
    """
    An unfilled swap offer.

    """
    def __init__(self, offer_dict):
        """
        Args:
            offer_dict: Dictionary of data for a single swap offer as returned
                by the Bitfinex API.

        """
        self.id = offer_dict["id"]
        self.currency = offer_dict["currency"]
        self.rate = Decimal(offer_dict["rate"])
        self.submitted_at = datetime.utcfromtimestamp(int(Decimal(
            offer_dict["timestamp"]
        )))
        self.amount = Decimal(offer_dict["remaining_amount"])

    def __repr__(self):
        return (
            "Offer(id={}, currency='{}', rate={}, amount={}, submitted_at={})"
        ).format(self.id, self.currency, self.rate, self.amount,
                 self.submitted_at)

    def get_new_rate(self):
        """
        Calculate what the interest rate on this offer should be changed to,
        based on how much time has elapsed since it was submitted.

        Returns:
            The new interest rate as a Decimal object, or None if the rate
            should not be changed.

        """
        min_rate, rate_decrement, decrement_interval = None, None, None
        if self.currency == "USD":
            min_rate = 0
            rate_decrement = 1
            decay_multiplier = 1
            decrement_interval = 1
        elif self.currency == "BTC":
            min_rate = 1
            rate_decrement = 1
            decay_multiplier = 1
            decrement_interval = 1
        else:
            raise Exception("Unrecognized currency string")

        if self.rate <= min_rate:
            return None
        time_elapsed = datetime.utcnow() - self.submitted_at
        intervals_elapsed = time_elapsed // decrement_interval
        if intervals_elapsed < 1:
            return None
        new_rate = self.rate
        for i in range(intervals_elapsed):
            # Apply the linear reduction first, then the exponential. If the
            # user didn't do something weird with the configuration, only one
            # will actually have an effect.
            new_rate -= rate_decrement
            # Asymptote at min_rate rather than at zero
            new_rate = (new_rate - min_rate) * decay_multiplier + min_rate
        return max(new_rate, min_rate)

class BitfinexAPI(object):
    """
    Handles API requests and responses.

    """
    base_url = "https://api.bitfinex.com"
    rate_limit_interval = timedelta(seconds=70)
    max_requests_per_interval = 60

    def __init__(self, api_key, api_secret):
        """
        Args:
            api_key: The API key to use for requests made by this object.
            api_secret: THe API secret to use for requests made by this object.

        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.nonce = count(int(time.time()))
        self.request_timestamps = deque()

    def get_lendbook(self, currency="USD"):
        return self._get("/v1/lendbook/{0}".format(currency))

    def get_offers(self):
        """
        Retrieve current offers.

        Returns:
            A 2-tuple of lists. The first contains USD offers and the second
            contains BTC offers, as Offer objects.

        """
        offers_data = self._request("/v1/offers")
        usd_offers = []
        btc_offers = []
        for offer_dict in offers_data:
            # Ignore swap demands and FRR offers
            if (
                offer_dict["direction"] == "lend"
                and offer_dict["rate"] != "0.0"
            ):
                offer = Offer(offer_dict)
                if offer.currency == "USD":
                    usd_offers.append(offer)
                elif offer.currency == "BTC":
                    btc_offers.append(offer)
        return (usd_offers, btc_offers)

    def cancel_offer(self, offer):
        """
        Cancel an offer.

        Args:
            offer: The offer to cancel as an Offer object.

        Returns:
            An Offer object representing the now-cancelled offer.

        """
        return Offer(self._request("/v1/offer/cancel", {"offer_id": offer.id}))

    def new_offer(self, currency, amount, rate, period):
        """
        Create a new offer.

        Args:
            currency: Either "USD" or "BTC".
            amount: Amount of the offer as a Decimal object.
            rate: Interest rate of the offer per year, as a Decimal object.
            period: How many days to lend for.

        Returns:
            An Offer object representing the newly-created offer.

        """
        return Offer(self._request("/v1/offer/new", {
            "currency": currency,
            "amount": str(amount),
            "rate": str(rate),
            "period": period,
            "direction": "lend",
        }))

    def get_available_balances(self):
        """
        Retrieve available balances in deposit wallet.

        Returns:
            A the USD balance available and amount.

        """
        balances_data = self._request("/v1/balances")
        usd_available = 0
        usd_amount = 0
        for balance_data in balances_data:
            if balance_data["type"] == "deposit":
                if balance_data["currency"] == "usd":
                    usd_available = Decimal(balance_data["available"])
                    usd_amount = Decimal(balance_data["amount"])
        # return balances_data
        return (usd_available, usd_amount)

    def _get(self, request_type, parameters=None):
        self._rate_limiter()
        url = self.base_url + request_type
        if parameters is None:
            parameters = {}
        parameters.update({"request": request_type,
                           "nonce": str(next(self.nonce))})
        payload = base64.b64encode(json.dumps(parameters).encode())
        signature = hmac.new(self.api_secret, payload, hashlib.sha384)
        headers = {"X-BFX-APIKEY": self.api_key,
                   "X-BFX-PAYLOAD": payload,
                   "X-BFX-SIGNATURE": signature.hexdigest()}
        request = None
        retry_count = 0
        while request is None:
            status_string = None
            try:
                request = requests.get(url, headers=headers)
            except requests.exceptions.ConnectionError:
                status_string = "Connection failed,"
            if request and request.status_code == 500:
                request = None
                status_string = "500 internal server error,"
            if request is None:
                delay = 2 ** retry_count
                print(status_string, "sleeping for", delay,
                      "seconds before retrying")
                time.sleep(delay)
                retry_count += 1
                # I'm assuming that if we don't manage to connect, or we get a
                # 500 internal server error, it doesn't count against our
                # request limit. If this isn't the case, then we should call
                # _rate_limiter() here too.
        if request.status_code != 200:
            print(request.text)
            request.raise_for_status()
        return request.json()

    def _request(self, request_type, parameters=None):
        self._rate_limiter()
        url = self.base_url + request_type
        if parameters is None:
            parameters = {}
        parameters.update({"request": request_type,
                           "nonce": str(next(self.nonce))})
        payload = base64.b64encode(json.dumps(parameters).encode())
        signature = hmac.new(self.api_secret, payload, hashlib.sha384)
        headers = {"X-BFX-APIKEY": self.api_key,
                   "X-BFX-PAYLOAD": payload,
                   "X-BFX-SIGNATURE": signature.hexdigest()}
        request = None
        retry_count = 0
        while request is None:
            status_string = None
            try:
                request = requests.post(url, headers=headers)
            except requests.exceptions.ConnectionError:
                status_string = "Connection failed,"
            if request and request.status_code == 500:
                request = None
                status_string = "500 internal server error,"
            if request is None:
                delay = 2 ** retry_count
                print(status_string, "sleeping for", delay,
                      "seconds before retrying")
                time.sleep(delay)
                retry_count += 1
                # I'm assuming that if we don't manage to connect, or we get a
                # 500 internal server error, it doesn't count against our
                # request limit. If this isn't the case, then we should call
                # _rate_limiter() here too.
        if request.status_code != 200:
            print(request.text)
            request.raise_for_status()
        return request.json()

    def _rate_limiter(self):
        timestamps = self.request_timestamps
        while True:
            expire = datetime.utcnow() - self.rate_limit_interval
            while timestamps and timestamps[0] < expire:
                timestamps.popleft()
            if len(timestamps) >= self.max_requests_per_interval:
                delay = (timestamps[0] - expire).total_seconds()
                print("Request rate limit hit, sleeping for", delay, "seconds")
                time.sleep(delay)
            else:
                break
        timestamps.append(datetime.utcnow())


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
