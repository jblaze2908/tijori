import type { AccountKind, AssetClass } from "./types";

/** Account kinds as the member reads them, in the order the picker lists them (docs/api.md, accounts). */
export const KIND_LABEL: Record<AccountKind, string> = {
  bank: "Bank account",
  card: "Credit card",
  wallet: "Wallet",
  cash: "Cash",
  deposit: "Deposit (FD, RD)",
  holding: "Investments (stocks, funds)",
  retirement: "Retirement (EPF, PPF, NPS)",
  gold: "Gold",
  property: "Property",
  loan: "Loan",
  other_asset: "Other asset",
  other_liability: "Other liability",
};
export const KINDS = Object.keys(KIND_LABEL) as AccountKind[];

const KIND_SHORT: Record<AccountKind, string> = {
  bank: "Bank", card: "Credit card", wallet: "Wallet", cash: "Cash", deposit: "Deposit", holding: "Investments",
  retirement: "Retirement", gold: "Gold", property: "Property", loan: "Loan", other_asset: "Other", other_liability: "Other",
};

export const SUBTYPES: Partial<Record<AccountKind, [string, string][]>> = {
  deposit: [["fd", "Fixed deposit"], ["rd", "Recurring deposit"]],
  holding: [["stocks", "Stocks & ETFs"], ["mf", "Mutual funds"]],
  retirement: [["epf", "EPF"], ["ppf", "PPF"], ["nps", "NPS"]],
  loan: [["home", "Home loan"], ["car", "Car loan"], ["personal", "Personal loan"], ["education", "Education loan"], ["gold", "Gold loan"]],
};

export const CLASS_LABEL: Record<AssetClass, string> = {
  cash: "Cash & savings",
  deposits: "Deposits",
  equity: "Stocks & funds",
  retirement: "Retirement",
  gold: "Gold",
  property: "Property",
  credit: "Credit cards",
  loans: "Loans",
  other: "Other",
};
export const CLASSES = Object.keys(CLASS_LABEL) as AssetClass[];

/** Things most people here hold that no statement reaches: one click adds the account, then you set its value. */
export const PRESETS: { label: string; institution: string; kind: AccountKind; subtype?: string }[] = [
  { label: "EPF", institution: "EPF", kind: "retirement", subtype: "epf" },
  { label: "PPF", institution: "PPF", kind: "retirement", subtype: "ppf" },
  { label: "NPS", institution: "NPS", kind: "retirement", subtype: "nps" },
  { label: "Gold", institution: "Gold", kind: "gold" },
  { label: "Property", institution: "Property", kind: "property" },
  { label: "Home loan", institution: "Home loan", kind: "loan", subtype: "home" },
  { label: "Cash", institution: "Cash", kind: "cash" },
];

/** A short "what is it" under an account's name: the preset if it has one, else the kind; nothing when the name says it. */
export function kindLine(kind: AccountKind, subtype: string | null, label: string): string | null {
  const t = SUBTYPES[kind]?.find(([k]) => k === subtype)?.[1] ?? KIND_SHORT[kind];
  return label.toLowerCase().includes(t.toLowerCase()) ? null : t;
}
