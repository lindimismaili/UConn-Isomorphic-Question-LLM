import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const AA_NORMAL_TEXT = 4.5;
const CSS = readFileSync(fileURLToPath(new URL('./app.css', import.meta.url)), 'utf8');

function token(name) {
  const match = CSS.match(new RegExp(`--${name}:\\s*([^;]+);`));
  if (!match) throw new Error(`Missing CSS token --${name}`);
  return match[1].trim();
}

function hexToRgb(value) {
  const match = value.match(/^#([0-9a-f]{6})$/i);
  if (!match) throw new Error(`Expected six-digit hex color, received ${value}`);
  return [0, 2, 4].map((offset) => Number.parseInt(match[1].slice(offset, offset + 2), 16));
}

function rgbChannels(name) {
  const channels = token(name).split(',').map((channel) => Number(channel.trim()));
  if (channels.length !== 3 || channels.some((channel) => !Number.isFinite(channel))) {
    throw new Error(`Expected RGB channels in --${name}`);
  }
  return channels;
}

function composite(foreground, background, alpha) {
  return foreground.map((channel, index) => (
    channel * alpha + background[index] * (1 - alpha)
  ));
}

function relativeLuminance(rgb) {
  const linear = rgb.map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first, second) {
  const lighter = Math.max(relativeLuminance(first), relativeLuminance(second));
  const darker = Math.min(relativeLuminance(first), relativeLuminance(second));
  return (lighter + 0.05) / (darker + 0.05);
}

function alphaFromRgbaToken(name) {
  const match = token(name).match(
    new RegExp(`^rgba\\(var\\(--${name.replace('-glow', '-rgb')}\\),\\s*(0?\\.\\d+|1(?:\\.0+)?)\\)$`),
  );
  if (!match) throw new Error(`Expected rgba token --${name} to reference its RGB channels`);
  return Number(match[1]);
}

function gradientHexStops(name) {
  const match = token(name).match(/^linear-gradient\([\s\S]+\)$/);
  if (!match) throw new Error(`Expected linear gradient in --${name}`);
  return [...match[0].matchAll(/#[0-9a-f]{6}/gi)].map(([value]) => hexToRgb(value));
}

describe('normal-text color contrast', () => {
  const white = hexToRgb('#ffffff');
  const canvas = hexToRgb(token('color-bg'));

  it('keeps warning text AA-readable on white, canvas, and its strongest text tint', () => {
    const warning = hexToRgb(token('color-warning'));
    const warningChannels = rgbChannels('color-warning-rgb');
    const warningTintAlpha = alphaFromRgbaToken('color-warning-glow');

    expect(warningChannels).toEqual(warning);
    expect(contrastRatio(warning, white)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    expect(contrastRatio(warning, canvas)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    expect(
      contrastRatio(warning, composite(warningChannels, canvas, warningTintAlpha)),
    ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });

  it('keeps white primary-action text AA-readable at every actual gradient stop', () => {
    const stops = gradientHexStops('gradient-primary');
    expect(stops).toHaveLength(3);
    for (const stop of stops) {
      expect(contrastRatio(white, stop)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    }
  });

  it.each(['success', 'error'])(
    'keeps %s semantic-chip text AA-readable over its 22%% glow tint',
    (tone) => {
      const foreground = hexToRgb(token(`color-${tone}`));
      const channels = rgbChannels(`color-${tone}-rgb`);
      const tintAlpha = alphaFromRgbaToken(`color-${tone}-glow`);

      expect(channels).toEqual(foreground);
      expect(tintAlpha).toBe(0.22);
      expect(
        contrastRatio(foreground, composite(channels, canvas, tintAlpha)),
      ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    },
  );
});
