/**
 * UI primitive library — the template building blocks the whole site composes
 * from. Every primitive is a thin wrapper over a class documented in
 * frontend/DESIGN.md; none introduces new CSS. See the design-system showcase
 * (design-system.html) for a live catalog.
 *
 * Rule: compose features only from these primitives. Any new element must be
 * added here (and to the showcase) first, then used.
 */

export { Button, IconButton } from './Button.jsx';
export { TextInput, TextArea, Select } from './Field.jsx';
export { ToggleSwitch } from './ToggleSwitch.jsx';
export { Card, CardHeader, CardBody, SectionHeading } from './Card.jsx';
export { Badge, StatusChip, ScoreChip, Pill, Chip } from './Badge.jsx';
export { StatusDot, LogItem, LogList, EmptyState } from './Status.jsx';
export { Tabs } from './Tabs.jsx';
export { DataTable, KeyValueTable } from './Table.jsx';
export { AppShell, PageHeader, Banner, Footer } from './Shell.jsx';
export { Avatar, ChatMessage, ChatTranscript, TypingIndicator, Composer } from './Chat.jsx';
export { Stepper } from './Stepper.jsx';
// The canonical modal shell stays in components/modals; re-export so callers
// import all primitives from one place.
export { default as Modal } from '../modals/Modal.jsx';
