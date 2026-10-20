export interface TwoWayInputs { capital: number; oddsA: number; oddsB: number; feesPercent?: number; slippagePercent?: number; maximumSize?: number; }
export function calculateTwoWay(inputs: TwoWayInputs) {
  if (inputs.capital < 0 || inputs.oddsA <= 1 || inputs.oddsB <= 1) throw new Error("Capital must be non-negative and decimal odds must exceed 1.");
  const impliedProbabilitySum = 1 / inputs.oddsA + 1 / inputs.oddsB;
  const usableCapital = Math.min(inputs.capital, inputs.maximumSize ?? Number.POSITIVE_INFINITY);
  const allocationA = usableCapital * (1 / inputs.oddsA) / impliedProbabilitySum;
  const allocationB = usableCapital - allocationA;
  const grossPayout = usableCapital / impliedProbabilitySum;
  const grossProfit = grossPayout - usableCapital;
  const costs = usableCapital * (((inputs.feesPercent ?? 0) + (inputs.slippagePercent ?? 0)) / 100);
  const netProfit = grossProfit - costs;
  return { impliedProbabilitySum, usableCapital, allocationA, allocationB, grossPayout, grossProfit, costs, netProfit, netRoiPercent: usableCapital ? netProfit / usableCapital * 100 : 0 };
}
