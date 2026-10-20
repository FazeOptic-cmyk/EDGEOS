export interface WindowBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PersistedWindowState {
  schemaVersion: 1;
  bounds: WindowBounds;
  maximized: boolean;
}

export const DEFAULT_WINDOW_BOUNDS: Readonly<WindowBounds> = {
  x: 80,
  y: 60,
  width: 1480,
  height: 940,
};

export const MINIMUM_WINDOW_WIDTH = 1040;
export const MINIMUM_WINDOW_HEIGHT = 680;

function finiteInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value);
}

export function parsePersistedWindowState(raw: string): PersistedWindowState | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return null;
    const record = value as Record<string, unknown>;
    const bounds = record.bounds;
    if (record.schemaVersion !== 1 || typeof record.maximized !== "boolean"
        || !bounds || typeof bounds !== "object") return null;
    const candidate = bounds as Record<string, unknown>;
    if (![candidate.x, candidate.y, candidate.width, candidate.height].every(finiteInteger)) return null;
    const typed = candidate as unknown as WindowBounds;
    if (typed.width < MINIMUM_WINDOW_WIDTH || typed.height < MINIMUM_WINDOW_HEIGHT
        || typed.width > 16_384 || typed.height > 16_384) return null;
    return { schemaVersion: 1, bounds: typed, maximized: record.maximized };
  } catch {
    return null;
  }
}

function intersectionArea(left: WindowBounds, right: WindowBounds): number {
  const width = Math.max(0, Math.min(left.x + left.width, right.x + right.width) - Math.max(left.x, right.x));
  const height = Math.max(0, Math.min(left.y + left.height, right.y + right.height) - Math.max(left.y, right.y));
  return width * height;
}

/** Keep a restored window usable after monitor removal or resolution changes. */
export function visibleWindowBounds(
  saved: WindowBounds | null,
  workAreas: readonly WindowBounds[],
): WindowBounds {
  const primary = workAreas[0];
  if (!primary) return { ...DEFAULT_WINDOW_BOUNDS };
  const source = saved ?? DEFAULT_WINDOW_BOUNDS;
  const target = workAreas.find((area) => intersectionArea(source, area) >= 64 * 64) ?? primary;
  const width = Math.min(Math.max(source.width, MINIMUM_WINDOW_WIDTH), target.width);
  const height = Math.min(Math.max(source.height, MINIMUM_WINDOW_HEIGHT), target.height);
  return {
    width,
    height,
    x: Math.min(Math.max(source.x, target.x), target.x + target.width - width),
    y: Math.min(Math.max(source.y, target.y), target.y + target.height - height),
  };
}

export function windowStateDocument(bounds: WindowBounds, maximized: boolean): string {
  return `${JSON.stringify({ schemaVersion: 1, bounds, maximized })}\n`;
}
