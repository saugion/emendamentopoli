// Shared project types

export interface Amendment {
  id: string;            // dati.senato.it URI
  legislature: number;
  number: string;       // e.g. "1.30"
  type: string;         // E = amendment, G = order of the day
  committeeSeat: boolean;
  actUri: string;       // item under discussion (bill)
  textUrl: string;
  textXmlUrl: string;   // Akoma Ntoso
  // populated after fetching the text:
  date?: string;        // submission date (YYYY-MM-DD)
  text?: string;        // plain-text body of the amendment
  bill?: string;        // e.g. "Ddl 391"
  signatories?: string[];    // senators' surnames (first = proposer)
  signatoryUris?: string[];  // senato.it profile URLs, aligned with signatories ("" if unknown)
  parentNumber?: string;     // numero dell'emendamento padre (subemendamento "X/N" -> "X")
  parentId?: string;         // id (dati.senato.it URI) dell'emendamento padre, risolto via SPARQL
}

export interface Score {
  criterion: string;
  score: number;     // 0-10
  rationale: string;
}

export interface Evaluation {
  amendmentId: string;
  scores: Score[];
  totalScore: number;  // 0-10, weighted average
  summary: string;
  model: string;       // LLM model used (or "heuristic")
  timestamp: string;
}

export const RUBRIC: Record<string, number> = {
  trasparenza: 0.3,   // democratic transparency and legality
  salute: 0.25,        // impact on public health
  ambiente: 0.25,      // impact on environment and climate
  concretezza: 0.2,    // technical effectiveness, not vagueness
};
