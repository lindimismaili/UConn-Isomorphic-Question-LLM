import { Card, CardBody, CardHeader } from '../../components/ui/index.js';
import { Grid, Row, SizeBox, Spec, Stack, Swatch } from '../Specimen.jsx';

const COLOR_TOKENS = [
  '--color-navy',
  '--color-bg',
  '--color-bg-secondary',
  '--color-surface',
  '--color-surface-hover',
  '--color-border',
  '--color-border-hover',
  '--color-primary',
  '--color-primary-hover',
  '--color-secondary',
  '--color-accent',
  '--color-success',
  '--color-warning',
  '--color-error',
  '--color-info',
  '--color-text',
  '--color-text-secondary',
  '--color-text-muted',
];

const GRADIENT_TOKENS = ['--gradient-primary', '--gradient-surface', '--gradient-card'];
const SPACING_TOKENS = [
  '--spacing-xs',
  '--spacing-sm',
  '--spacing-md',
  '--spacing-lg',
  '--spacing-xl',
  '--spacing-2xl',
];
const RADIUS_TOKENS = ['--radius-sm', '--radius-md', '--radius-lg', '--radius-xl', '--radius-pill'];
const SHADOW_TOKENS = ['--shadow-sm', '--shadow-md', '--shadow-lg', '--shadow-glow'];
const MOTION_TOKENS = ['--transition-fast', '--transition-normal', '--transition-slow'];

/** Foundations: the design tokens every component derives from (DESIGN.md §1). */
export default function Foundations() {
  return (
    <Stack gap="var(--section-gap)">
      <h2 id="foundations" style={{ scrollMarginTop: '80px' }}>
        Foundations
      </h2>

      <Card>
        <CardHeader title="Color" subtitle="Every surface, border, and status reads from these tokens. Never hardcode hex." />
        <CardBody>
          <Grid min="180px">
            {COLOR_TOKENS.map((token) => (
              <Swatch key={token} token={token} />
            ))}
          </Grid>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Gradients" subtitle="Primary CTAs and large surfaces only; body buttons stay flat." />
        <CardBody>
          <Grid min="240px">
            {GRADIENT_TOKENS.map((token) => (
              <Swatch key={token} token={token} />
            ))}
          </Grid>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Typography" subtitle="--font-serif headings · --font-sans body · --font-mono for code/IDs. Family + size + line-height all tokenized." />
        <CardBody>
          <Stack>
            <h1>Heading 1 — Source Serif 4 (--font-serif)</h1>
            <h2>Heading 2 — Source Serif 4</h2>
            <h3>Heading 3 — Source Serif 4</h3>
            <h4>Heading 4 — Source Serif 4</h4>
            <p>Body — IBM Plex Sans (--font-sans). The quick brown fox jumps over the lazy dog.</p>
            <p className="subtitle">Subtitle — secondary text used under headers.</p>
            <p className="disclaimer-text">Disclaimer text — muted caption style.</p>
            <code style={{ fontFamily: 'var(--font-mono)' }}>
              Monospace (--font-mono) — workflow-1a2b · 12:00:00
            </code>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Spacing" subtitle="--spacing-xs … 2xl plus semantic panel/section gaps." />
        <CardBody>
          <Row align="flex-end">
            {SPACING_TOKENS.map((token) => (
              <SizeBox key={token} token={token} axis="width" />
            ))}
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Radius" subtitle="Controls = sm · text buttons = md · containers = lg." />
        <CardBody>
          <Row>
            {RADIUS_TOKENS.map((token) => (
              <SizeBox key={token} token={token} axis="radius" />
            ))}
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Elevation & shadow" subtitle="Depth + the primary glow halo." />
        <CardBody>
          <Row gap="var(--spacing-lg)">
            {SHADOW_TOKENS.map((token) => (
              <Spec key={token} label={token}>
                <div
                  style={{
                    width: 120,
                    height: 64,
                    borderRadius: 'var(--radius-md)',
                    background: 'var(--color-surface)',
                    boxShadow: `var(${token})`,
                  }}
                />
              </Spec>
            ))}
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Motion" subtitle="Hover the tiles — durations from the motion tokens (safe properties only)." />
        <CardBody>
          <Row gap="var(--spacing-lg)">
            {MOTION_TOKENS.map((token) => (
              <Spec key={token} label={token}>
                <div
                  className="ds-motion-demo"
                  style={{
                    width: 96,
                    height: 48,
                    borderRadius: 'var(--radius-md)',
                    background: 'var(--gradient-primary)',
                    transition: `transform var(${token}), box-shadow var(${token})`,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.transform = 'translateY(-6px)';
                    e.currentTarget.style.boxShadow = 'var(--shadow-glow)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.transform = 'none';
                    e.currentTarget.style.boxShadow = 'none';
                  }}
                />
              </Spec>
            ))}
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Layout" subtitle="Content centers within --site-max-width (1760px); panels collapse at documented breakpoints (1200/1100/900/820/768/700)." />
        <CardBody>
          <p className="subtitle">
            Two-column grids (.top-panels, .pipeline-split) stack to one column at ≤1100px. New
            responsive rules reuse the existing breakpoints — don't add arbitrary ones.
          </p>
        </CardBody>
      </Card>
    </Stack>
  );
}
