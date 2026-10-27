// The page's two languages, without an i18n library: one typed dictionary per language, with the
// same keys. Card titles, quotes and notes, and the model's reasons, are never translated: they are
// the evidence as written, and the citation check depends on their exact text.
import { createContext, useContext } from "react";
import type { ProblemKind } from "./api/problems";
import type { ActiveStatus, Proof, RequirementType, Verdict } from "./api/types";

export type Lang = "en" | "pt";
export const LANGS: Lang[] = ["en", "pt"];
export const LANG_STORAGE_KEY = "fitcheck.lang";

export interface Messages {
  htmlLang: string;
  locale: string;
  documentTitle: string;
  skipToContent: string;
  languageGroup: string;
  languageNames: Record<Lang, string>;
  tagline: string;
  profileNav: string;
  analyzeSection: string;
  form: {
    label: string;
    placeholder: string;
    count: (count: string, max: string) => string;
    atLeast: (min: number) => string;
    atMost: (max: string) => string;
    ready: string;
    privacy: string;
    analyze: string;
    analyzing: string;
  };
  progress: {
    heading: string;
    sending: string;
    working: string;
    steps: Record<ActiveStatus, string>;
    done: string;
    active: string;
    waiting: string;
    found: (count: number) => string;
  };
  verdicts: Record<Verdict, string>;
  types: Record<RequirementType, string>;
  proof: Record<Proof["type"], string>;
  requirement: {
    inPosting: string;
    inEnglish: string;
    notVerified: string;
    citedCards: string;
  };
  result: {
    heading: string;
    score: string;
    none: string;
    counts: (total: number, meets: number, partial: number, miss: number) => string;
    analyzed: string;
    cost: string;
    latency: string;
    model: string;
  };
  problems: Record<ProblemKind, { title: string; body: string }>;
  problem: {
    code: (code: string) => string;
    retry: string;
    examples: string;
  };
  examples: {
    heading: string;
    intro: string;
    names: Record<string, string>;
    summary: (score: string, count: number) => string;
    recorded: (date: string) => string;
  };
  how: {
    heading: string;
    steps: string[];
    quality: string;
    measured: (count: number, date: string) => string;
    accuracy: string;
    overestimation: string;
    severe: string;
    source: string;
    adrs: string;
  };
  footer: {
    by: string;
    source: string;
  };
}

const en: Messages = {
  htmlLang: "en",
  locale: "en-US",
  documentTitle: "Fit Check: Khaled Haikel, job fit",
  skipToContent: "Skip to content",
  languageGroup: "Language",
  languageNames: { en: "English", pt: "Portuguese" },
  tagline: "Paste a job description and see, requirement by requirement, whether Khaled Haikel meets it, with the evidence.",
  profileNav: "Profile",
  analyzeSection: "Analyze a job description",
  form: {
    label: "Job description",
    placeholder: "Paste the full job posting here.",
    count: (count, max) => `${count} / ${max} characters.`,
    atLeast: (min) => `At least ${min} characters.`,
    atMost: (max) => `At most ${max} characters.`,
    ready: "Ready to analyze.",
    privacy:
      "Job descriptions are stored for 30 days to serve cached results. Your IP address is kept only as a hash, for two days, to apply the daily limit.",
    analyze: "Analyze",
    analyzing: "Analyzing...",
  },
  progress: {
    heading: "Analyzing",
    sending: "Sending the job description",
    working: "Working",
    steps: {
      queued: "Queued",
      extracting: "Extracting requirements",
      retrieving: "Finding evidence",
      judging: "Judging",
    },
    done: "(done)",
    active: "(in progress)",
    waiting: "(waiting)",
    found: (count) => `Requirements found (${count})`,
  },
  verdicts: { meets: "Meets", partial: "Partially meets", does_not_meet: "Does not meet" },
  types: { must: "Must have", nice: "Nice to have" },
  proof: { repo: "Repository", live: "Live site", demo: "Demo", doc: "Document", none: "No public link" },
  requirement: {
    inPosting: "In the job description: ",
    inEnglish: "In English: ",
    notVerified: "Not verified against the evidence",
    citedCards: "Cited cards",
  },
  result: {
    heading: "Result",
    score: "Fit score",
    none: "None listed",
    counts: (total, meets, partial, miss) =>
      `${total}: ${meets} ${meets === 1 ? "meets" : "meet"}, ${partial} partial, ${miss} ${miss === 1 ? "does not meet" : "do not meet"}`,
    analyzed: "Analyzed",
    cost: "Cost",
    latency: "Latency",
    model: "Model",
  },
  problems: {
    daily_limit: {
      title: "Daily limit reached",
      body: "The daily limit of new analyses has been reached. Try again tomorrow, or open one of the examples below.",
    },
    monthly_budget: {
      title: "Monthly budget used up",
      body: "This month's public budget for analyses has been used up. Open one of the examples below.",
    },
    busy: {
      title: "Too many analyses running",
      body: "Too many analyses are in progress right now. Try again in a few minutes.",
    },
    rate_limited: { title: "Too many requests", body: "Wait a moment and try again." },
    invalid: {
      title: "The job description was not accepted",
      body: "It needs 200 to 20,000 characters. Check the text and try again.",
    },
    unavailable: { title: "Analysis is unavailable", body: "The service cannot run analyses right now. Try again later." },
    not_found: { title: "Analysis not found", body: "This analysis no longer exists. Try again." },
    failed: { title: "The analysis failed", body: "Something went wrong while analyzing this job description. Try again." },
    timed_out: { title: "The analysis stopped", body: "The analysis stopped responding before it finished. Try again." },
    poll_timeout: {
      title: "Still running",
      body: "The analysis is taking more than five minutes. Try again in a few minutes to see the result.",
    },
    network: { title: "Could not reach the service", body: "Check your connection and try again." },
    not_configured: {
      title: "Analysis is not configured",
      body: "This build of the page has no API address. Open one of the examples below.",
    },
  },
  problem: {
    code: (code) => `Error code: ${code}`,
    retry: "Try again",
    examples: "See the examples",
  },
  examples: {
    heading: "Examples",
    intro: "Two analyses recorded through the public API. Opening one does not run a new analysis.",
    names: {
      "dev-01-ai-automation-latam": "Remote AI Automation Engineer (LLM Systems and Automation)",
      "dev-02-fullstack-ai-native": "Senior Full-Stack Engineer (Node.js and Next.js, AI-native)",
    },
    summary: (score, count) => `Score ${score} / 100, ${count} requirements`,
    recorded: (date) => `Recorded on ${date} with analysis_version`,
  },
  how: {
    heading: "How it works",
    steps: [
      "Claude Sonnet 4.6 on Amazon Bedrock extracts the requirements of the job description. Each one keeps the excerpt it came from.",
      "A hybrid search, by keywords and by meaning, finds evidence cards for each requirement.",
      "Claude judges each requirement against those cards: meets, partially meets or does not meet.",
      "Code checks every citation: the card must be among those retrieved for that requirement and the quote must appear in it verbatim. A verdict without a valid citation becomes does not meet.",
      "When a reply from the model cannot be validated, the analysis fails instead of guessing.",
    ],
    quality: "How well it judges",
    measured: (count, date) => `Measured once on a held-out test split of ${count} labeled requirements, on ${date}.`,
    accuracy: "Judgment accuracy",
    overestimation: "Overestimation: a verdict above the label",
    severe: "Severe overestimation: meets where the label is does not meet",
    source: "Source code",
    adrs: "Architecture decision records",
  },
  footer: {
    by: "Fit Check by Khaled Haikel.",
    source: "Source code",
  },
};

const pt: Messages = {
  htmlLang: "pt-BR",
  locale: "pt-BR",
  documentTitle: "Fit Check: Khaled Haikel, aderência à vaga",
  skipToContent: "Pular para o conteúdo",
  languageGroup: "Idioma",
  languageNames: { en: "Inglês", pt: "Português" },
  tagline: "Cole a descrição de uma vaga e veja, requisito por requisito, se o Khaled Haikel atende, com a evidência.",
  profileNav: "Perfil",
  analyzeSection: "Analisar a descrição de uma vaga",
  form: {
    label: "Descrição da vaga",
    placeholder: "Cole aqui a descrição completa da vaga.",
    count: (count, max) => `${count} / ${max} caracteres.`,
    atLeast: (min) => `Pelo menos ${min} caracteres.`,
    atMost: (max) => `No máximo ${max} caracteres.`,
    ready: "Pronto para analisar.",
    privacy:
      "A descrição da vaga fica guardada por 30 dias para responder pelo cache. Seu endereço IP é guardado só como hash, por dois dias, para aplicar o limite diário.",
    analyze: "Analisar",
    analyzing: "Analisando...",
  },
  progress: {
    heading: "Analisando",
    sending: "Enviando a descrição da vaga",
    working: "Processando",
    steps: {
      queued: "Na fila",
      extracting: "Extraindo os requisitos",
      retrieving: "Buscando evidências",
      judging: "Avaliando",
    },
    done: "(concluído)",
    active: "(em andamento)",
    waiting: "(aguardando)",
    found: (count) => `Requisitos encontrados (${count})`,
  },
  verdicts: { meets: "Atende", partial: "Atende em parte", does_not_meet: "Não atende" },
  types: { must: "Obrigatório", nice: "Desejável" },
  proof: { repo: "Repositório", live: "Site no ar", demo: "Demonstração", doc: "Documento", none: "Sem link público" },
  requirement: {
    inPosting: "Na descrição da vaga: ",
    inEnglish: "Em inglês: ",
    notVerified: "Não verificado com a evidência",
    citedCards: "Fichas citadas (texto original em inglês)",
  },
  result: {
    heading: "Resultado",
    score: "Aderência",
    none: "Nenhum na vaga",
    counts: (total, meets, partial, miss) =>
      `${total}: ${meets} ${meets === 1 ? "atende" : "atendem"}, ${partial} em parte, ${miss} ${miss === 1 ? "não atende" : "não atendem"}`,
    analyzed: "Analisada em",
    cost: "Custo",
    latency: "Latência",
    model: "Modelo",
  },
  problems: {
    daily_limit: {
      title: "Limite diário atingido",
      body: "O limite diário de análises novas foi atingido. Tente de novo amanhã ou abra um dos exemplos abaixo.",
    },
    monthly_budget: {
      title: "Orçamento do mês esgotado",
      body: "O orçamento público de análises deste mês acabou. Abra um dos exemplos abaixo.",
    },
    busy: {
      title: "Muitas análises em andamento",
      body: "Há muitas análises em andamento agora. Tente de novo em alguns minutos.",
    },
    rate_limited: { title: "Muitas requisições", body: "Espere um pouco e tente de novo." },
    invalid: {
      title: "A descrição da vaga não foi aceita",
      body: "Ela precisa ter de 200 a 20.000 caracteres. Confira o texto e tente de novo.",
    },
    unavailable: { title: "Análise indisponível", body: "O serviço não consegue fazer análises agora. Tente de novo mais tarde." },
    not_found: { title: "Análise não encontrada", body: "Esta análise não existe mais. Tente de novo." },
    failed: { title: "A análise falhou", body: "Algo deu errado ao analisar esta descrição de vaga. Tente de novo." },
    timed_out: { title: "A análise parou", body: "A análise parou de responder antes de terminar. Tente de novo." },
    poll_timeout: {
      title: "Ainda em andamento",
      body: "A análise está levando mais de cinco minutos. Tente de novo em alguns minutos para ver o resultado.",
    },
    network: { title: "Não foi possível falar com o serviço", body: "Confira sua conexão e tente de novo." },
    not_configured: {
      title: "Análise não configurada",
      body: "Esta versão da página não tem o endereço da API. Abra um dos exemplos abaixo.",
    },
  },
  problem: {
    code: (code) => `Código do erro: ${code}`,
    retry: "Tentar de novo",
    examples: "Ver os exemplos",
  },
  examples: {
    heading: "Exemplos",
    intro: "Duas análises gravadas pela API pública. Abrir uma delas não faz uma análise nova.",
    names: {
      "dev-01-ai-automation-latam": "Engenheiro de Automação com IA, remoto (sistemas com LLM e automação)",
      "dev-02-fullstack-ai-native": "Engenheiro Full-Stack Sênior (Node.js e Next.js, nativo em IA)",
    },
    summary: (score, count) => `Aderência ${score} / 100, ${count} requisitos`,
    recorded: (date) => `Gravada em ${date} com analysis_version`,
  },
  how: {
    heading: "Como funciona",
    steps: [
      "O Claude Sonnet 4.6 no Amazon Bedrock extrai os requisitos da descrição da vaga. Cada um guarda o trecho de onde veio.",
      "Uma busca híbrida, por palavras-chave e por significado, encontra fichas de evidência para cada requisito.",
      "O Claude avalia cada requisito com base nessas fichas: atende, atende em parte ou não atende.",
      "O código confere cada citação: a ficha precisa estar entre as recuperadas para aquele requisito, e o trecho citado precisa aparecer nela palavra por palavra. Um veredito sem citação válida vira não atende.",
      "Quando uma resposta do modelo não passa na validação, a análise falha em vez de adivinhar.",
    ],
    quality: "Qualidade do julgamento",
    measured: (count, date) =>
      `Medido uma vez em um conjunto de teste separado, com ${count} requisitos rotulados, em ${date}.`,
    accuracy: "Acurácia do julgamento",
    overestimation: "Superestimação: veredito acima do rótulo",
    severe: "Superestimação grave: atende onde o rótulo é não atende",
    source: "Código-fonte",
    adrs: "Registros de decisões de arquitetura",
  },
  footer: {
    by: "Fit Check, de Khaled Haikel.",
    source: "Código-fonte",
  },
};

export const MESSAGES: Record<Lang, Messages> = { en, pt };

function isLang(value: string | null | undefined): value is Lang {
  return value === "en" || value === "pt";
}

// The first language that applies: ?lang= in the URL, the last choice saved, then the browser's.
export function pickLang(search: string, stored: string | null, browser: readonly string[]): Lang {
  const fromUrl = new URLSearchParams(search).get("lang");
  if (isLang(fromUrl)) return fromUrl;
  if (isLang(stored)) return stored;
  return browser[0]?.toLowerCase().startsWith("pt") ? "pt" : "en";
}

type StorageAccess = () => Pick<Storage, "getItem" | "setItem">;
const browserStorage: StorageAccess = () => window.localStorage;

// Storage can be missing or throw (private windows, blocked site data); the page works without it.
export function readStoredLang(storage: StorageAccess = browserStorage): string | null {
  try {
    return storage().getItem(LANG_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function saveLang(lang: Lang, storage: StorageAccess = browserStorage): void {
  try {
    storage().setItem(LANG_STORAGE_KEY, lang);
  } catch {
    // The choice is just not remembered.
  }
}

export function detectLang(): Lang {
  if (typeof window === "undefined") return "en";
  const browser = navigator.languages?.length ? navigator.languages : [navigator.language];
  return pickLang(window.location.search, readStoredLang(), browser);
}

// The same address with ?lang= set, keeping the other parameters and the fragment.
export function withLang(href: string, lang: Lang): string {
  const url = new URL(href);
  url.searchParams.set("lang", lang);
  return `${url.pathname}${url.search}${url.hash}`;
}

export const LangContext = createContext<Lang>("en");

export function useLang(): { lang: Lang; t: Messages } {
  const lang = useContext(LangContext);
  return { lang, t: MESSAGES[lang] };
}
