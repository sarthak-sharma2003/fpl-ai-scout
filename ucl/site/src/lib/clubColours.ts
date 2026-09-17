/** Club kit colours, keyed by UEFA team id.
 *
 * `primary` and `secondary` are the two colours a club is actually recognised
 * by — Barcelona's blue and garnet, Villarreal's yellow and navy — so a card
 * can wear its kit rather than a generic position stripe. Clubs that play in a
 * single dominant colour (Liverpool, Bayern) get a sensible trim as secondary.
 *
 * `onLight` marks kits whose primary is pale enough that dark text is needed on
 * it — Real Madrid, Leipzig, Stuttgart, LASK's away white, Tottenham-style
 * whites. Without it, white-on-white is unreadable.
 *
 * Colours only. No crests, no wordmarks: those are trademarks, and a club
 * colour is just a colour.
 */
export type ClubKit = {
  primary: string;
  secondary: string;
  /** primary is light — use dark ink on top of it */
  onLight?: boolean;
};

export const CLUB_KITS: Record<number, ClubKit> = {
  50129: { primary: "#f2c200", secondary: "#101010", onLight: true }, // AEK Athens
  52280: { primary: "#ef0107", secondary: "#f5f5f5" }, // Arsenal
  52683: { primary: "#670e36", secondary: "#95bfe5" }, // Aston Villa
  50124: { primary: "#cb3524", secondary: "#1d2b5c" }, // Atlético
  52758: { primary: "#fde100", secondary: "#101010", onLight: true }, // Dortmund
  50080: { primary: "#004d98", secondary: "#a50044" }, // Barcelona
  50037: { primary: "#dc052d", secondary: "#0066b2" }, // Bayern
  59333: { primary: "#ffe500", secondary: "#101010", onLight: true }, // Bodø/Glimt
  50043: { primary: "#005ca9", secondary: "#101010" }, // Club Brugge
  79946: { primary: "#0b3d91", secondary: "#f5f5f5" }, // Como
  52692: { primary: "#163962", secondary: "#ffed00" }, // Fenerbahçe
  52749: { primary: "#e30613", secondary: "#f5f5f5" }, // Feyenoord
  50067: { primary: "#a90432", secondary: "#fbb800" }, // Galatasaray
  50138: { primary: "#0068a8", secondary: "#101010" }, // Inter
  63405: { primary: "#101010", secondary: "#f5f5f5" }, // LASK
  2603790: { primary: "#dd0741", secondary: "#f5f5f5" }, // Leipzig
  52277: { primary: "#ffe500", secondary: "#e1000f", onLight: true }, // Lens
  75797: { primary: "#e01e13", secondary: "#0b2265" }, // Lille
  7889: { primary: "#c8102e", secondary: "#00b2a9" }, // Liverpool
  52919: { primary: "#6cabdd", secondary: "#1c2c5b", onLight: true }, // Man City
  52682: { primary: "#da291c", secondary: "#ffe500" }, // Man Utd
  50136: { primary: "#12a0d7", secondary: "#f5f5f5" }, // Napoli
  50062: { primary: "#ed1c24", secondary: "#f5f5f5" }, // PSV
  52747: { primary: "#004170", secondary: "#da291c" }, // Paris
  50064: { primary: "#00428c", secondary: "#f5f5f5" }, // Porto
  52265: { primary: "#00954c", secondary: "#f5f5f5" }, // Real Betis
  50051: { primary: "#f0f0f0", secondary: "#febe10", onLight: true }, // Real Madrid
  50137: { primary: "#8e1f2f", secondary: "#f0bc42" }, // Roma
  52797: { primary: "#009ee0", secondary: "#f5f5f5" }, // Slovan Bratislava
  2609356: { primary: "#0b7a3e", secondary: "#f5f5f5" }, // Sabah
  52707: { primary: "#ff8000", secondary: "#101010", onLight: true }, // Shakhtar
  52498: { primary: "#d31b23", secondary: "#f5f5f5" }, // Slavia Praha
  50149: { primary: "#008057", secondary: "#f5f5f5" }, // Sporting CP
  50107: { primary: "#f0f0f0", secondary: "#e32219", onLight: true }, // Stuttgart
  52319: { primary: "#003d7c", secondary: "#f5f5f5" }, // Viking
  70691: { primary: "#ffe667", secondary: "#005187", onLight: true }, // Villarreal
};

/** Neutral fallback for a club we have no kit for (knockout newcomers, or a
 * feed id that changes). Never throw — an unknown club still renders. */
export const DEFAULT_KIT: ClubKit = { primary: "#6f8f7d", secondary: "#21402f" };

export function kitFor(teamId: number): ClubKit {
  return CLUB_KITS[teamId] ?? DEFAULT_KIT;
}
