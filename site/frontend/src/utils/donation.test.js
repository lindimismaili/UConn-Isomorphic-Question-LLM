import { describe, expect, it } from 'vitest';

import { getSafeDonationUrl } from './donation.js';

describe('getSafeDonationUrl', () => {
  it('accepts HTTPS PayPal and PayPal.me hosts', () => {
    expect(getSafeDonationUrl('https://www.paypal.com/donate/example'))
      .toBe('https://www.paypal.com/donate/example');
    expect(getSafeDonationUrl('https://paypal.me/example'))
      .toBe('https://paypal.me/example');
  });

  it('rejects non-HTTPS and deceptive hosts', () => {
    expect(getSafeDonationUrl('http://paypal.me/example')).toBe('');
    expect(getSafeDonationUrl('https://paypal.com.example.org/donate')).toBe('');
    expect(getSafeDonationUrl('https://user:password@paypal.com/donate')).toBe('');
    expect(getSafeDonationUrl('https://paypal.com:8443/donate')).toBe('');
    expect(getSafeDonationUrl('https://paypal.com/donate link')).toBe('');
    expect(getSafeDonationUrl('not a URL')).toBe('');
  });
});
