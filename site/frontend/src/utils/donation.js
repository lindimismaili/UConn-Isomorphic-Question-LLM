/** Accept only optional HTTPS PayPal-hosted donation links. */
export function getSafeDonationUrl(value) {
  if (
    typeof value !== 'string'
    || !value.trim()
    || [...value].some((character) => /\s/.test(character) || character.charCodeAt(0) < 32)
  ) return '';
  try {
    const url = new URL(value.trim());
    const host = url.hostname.toLowerCase();
    const paypalHost =
      host === 'paypal.com' ||
      host.endsWith('.paypal.com') ||
      host === 'paypal.me' ||
      host.endsWith('.paypal.me');
    const safeAuthority =
      !url.username
      && !url.password
      && (!url.port || url.port === '443');
    return url.protocol === 'https:' && paypalHost && safeAuthority ? url.href : '';
  } catch {
    return '';
  }
}
