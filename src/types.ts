// Tipi condivisi del progetto

export interface Emendamento {
  id: string;            // URI dati.senato.it
  legislatura: number;
  numero: string;       // es. "1.30"
  tipo: string;         // E = emendamento, G = ordine del giorno
  sedeCommissione: boolean;
  attoUri: string;      // oggetto della trattazione (ddl)
  urlTesto: string;
  urlTestoXml: string;  // Akoma Ntoso
  // popolati dopo il fetch del testo:
  data?: string;        // data di presentazione (YYYY-MM-DD)
  testo?: string;       // testo plain-text dell'emendamento
  ddl?: string;         // es. "Ddl 391"
  firmatari?: string[]; // cognomi dei senatori firmatari (il primo e' il proponente)
  firmatari_uri?: string[]; // URL profilo senato.it, allineati a firmatari ("" se ignoto)
}

export interface Punteggio {
  criterio: string;
  punteggio: number;   // 0-10
  motivazione: string;
}

export interface Valutazione {
  emendamentoId: string;
  punteggi: Punteggio[];
  scoreTotale: number;  // 0-10, media pesata
  riepilogo: string;
  modello: string;     // modello LLM usato (o "euristica")
  timestamp: string;
}

export const RUBRICA: Record<string, number> = {
  trasparenza: 0.3,   // trasparenza democratica e legalita'
  salute: 0.25,        // impatto su salute pubblica
  ambiente: 0.25,      // impatto su ambiente e clima
  concretezza: 0.2,    // efficacia tecnica, non genericita'
};
