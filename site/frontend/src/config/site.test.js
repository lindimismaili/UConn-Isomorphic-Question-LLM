import { describe, expect, it } from 'vitest';
import {
  PAYPAL_DONATION_EMAIL,
  PAYPAL_DONATION_URL,
  buildPayPalDonationUrl,
} from './site.js';

describe('site configuration', () => {
  it('uses the configured PayPal recipient in the donation URL', () => {
    const url = new URL(PAYPAL_DONATION_URL);

    expect(PAYPAL_DONATION_EMAIL).toBe('Lindim.ismaili0001@gmail.com');
    expect(url.origin).toBe('https://www.paypal.com');
    expect(url.pathname).toBe('/cgi-bin/webscr');
    expect(url.searchParams.get('cmd')).toBe('_donations');
    expect(url.searchParams.get('business')).toBe(PAYPAL_DONATION_EMAIL);
    expect(url.searchParams.get('currency_code')).toBe('USD');
  });

  it('builds the same donation link for another confirmed PayPal email', () => {
    const url = new URL(buildPayPalDonationUrl('professor@example.edu'));

    expect(url.searchParams.get('business')).toBe('professor@example.edu');
  });
});
