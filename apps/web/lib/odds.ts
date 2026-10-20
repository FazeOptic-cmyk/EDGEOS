export function americanToDecimal(american: number): number {
  if (!Number.isFinite(american) || Math.abs(american) < 100) throw new Error("American odds must be at least +100 or at most −100.");
  return american > 0 ? 1 + american / 100 : 1 + 100 / Math.abs(american);
}

export function fractionalToDecimal(fractional: string): number {
  const match = fractional.trim().match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/);
  if (!match) throw new Error("Use fractional odds such as 10/11.");
  const numerator = Number(match[1]);
  const denominator = Number(match[2]);
  if (numerator <= 0) throw new Error("The fractional numerator must be positive.");
  if (denominator <= 0) throw new Error("The fractional denominator must be positive.");
  return 1 + numerator / denominator;
}

export function impliedProbabilityPercent(decimalOdds: number): number {
  if (!Number.isFinite(decimalOdds) || decimalOdds <= 1) throw new Error("Decimal odds must be greater than 1.");
  return 100 / decimalOdds;
}

export function removeVig(decimalOdds: number[]): number[] {
  const raw = decimalOdds.map((odds) => 1 / odds);
  const total = raw.reduce((sum, probability) => sum + probability, 0);
  if (!Number.isFinite(total) || total <= 0 || decimalOdds.some((odds) => odds < 1)) throw new Error("All decimal equivalents must be at least 1.");
  return raw.map((probability) => probability / total);
}

export function expectedValue(decimalOdds: number, probabilityPercent: number, stake: number): number {
  if (probabilityPercent < 0 || probabilityPercent > 100 || stake < 0) throw new Error("Probability must be 0–100 and stake cannot be negative.");
  const probability = probabilityPercent / 100;
  return probability * stake * (decimalOdds - 1) - (1 - probability) * stake;
}

export type OddsInputFormat = "decimal" | "american" | "fractional" | "contract";

export interface NormalizedOddsInput {
  priceKind: "decimal_odds" | "contract_ask";
  /** Exact value sent to the Decimal API after input normalization. */
  price: string;
  /** Decimal-odds equivalent used only for transparent implied-probability research. */
  decimalEquivalent: number;
}

function formattedDecimal(value: number): string {
  if (!Number.isFinite(value) || value <= 0 || value > 1_000_000_000) {
    throw new Error("The entered price is outside the supported range.");
  }
  return value.toFixed(12).replace(/\.?0+$/u, "");
}

function canonicalDecimal(value: string): string {
  const [whole, fraction = ""] = value.split(".");
  const normalizedWhole = whole.replace(/^0+(?=\d)/u, "");
  const normalizedFraction = fraction.replace(/0+$/u, "");
  return normalizedFraction ? `${normalizedWhole}.${normalizedFraction}` : normalizedWhole;
}

/** Normalize a user-entered display format into the API's explicit price kind. */
export function normalizeOddsInput(format: OddsInputFormat, raw: string): NormalizedOddsInput {
  const value = raw.trim();
  if (!value) throw new Error("Enter a price for every outcome.");

  if (format === "american") {
    if (!/^[+-]?\d+(?:\.\d+)?$/u.test(value)) throw new Error("Use American odds such as -110 or +150.");
    const decimalEquivalent = americanToDecimal(Number(value));
    const price = formattedDecimal(decimalEquivalent);
    if (Number(price) <= 1) throw new Error("The entered American odds exceed the supported precision.");
    return { priceKind: "decimal_odds", price, decimalEquivalent };
  }

  if (format === "fractional") {
    const decimalEquivalent = fractionalToDecimal(value);
    const price = formattedDecimal(decimalEquivalent);
    if (Number(price) <= 1) throw new Error("The entered fractional odds exceed the supported precision.");
    return { priceKind: "decimal_odds", price, decimalEquivalent };
  }

  if (!/^\d+(?:\.\d+)?$/u.test(value)) {
    throw new Error(format === "contract" ? "Use a contract ask between 0 and 1." : "Use decimal odds greater than 1.");
  }
  const numeric = Number(value);
  if (format === "contract") {
    if (!Number.isFinite(numeric) || numeric <= 0 || numeric > 1) throw new Error("Contract asks must be greater than 0 and at most 1.");
    return { priceKind: "contract_ask", price: canonicalDecimal(value), decimalEquivalent: 1 / numeric };
  }
  if (!Number.isFinite(numeric) || numeric <= 1) throw new Error("Decimal odds must be greater than 1.");
  return { priceKind: "decimal_odds", price: canonicalDecimal(value), decimalEquivalent: numeric };
}
