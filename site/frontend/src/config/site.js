/**
 * Public site configuration.
 *
 * Vite exposes `VITE_*` values in the browser bundle, so the donation recipient
 * is intentionally public. Change the default below or set
 * VITE_PAYPAL_DONATION_EMAIL at build time to use another confirmed PayPal
 * account email.
 */
const DEFAULT_PAYPAL_DONATION_EMAIL = 'Lindim.ismaili0001@gmail.com';

export const PAYPAL_DONATION_EMAIL =
  import.meta.env.VITE_PAYPAL_DONATION_EMAIL?.trim() || DEFAULT_PAYPAL_DONATION_EMAIL;

export function buildPayPalDonationUrl(recipient = PAYPAL_DONATION_EMAIL) {
  const url = new URL('https://www.paypal.com/cgi-bin/webscr');
  url.searchParams.set('cmd', '_donations');
  url.searchParams.set('business', recipient);
  url.searchParams.set('currency_code', 'USD');
  url.searchParams.set('item_name', 'UConn Isomorphic Question Generator');
  return url.toString();
}

export const PAYPAL_DONATION_URL = buildPayPalDonationUrl();
