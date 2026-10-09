/**
 * Universities a student can pick when registering for the free plan.
 *
 * Roughly the top of the major world rankings, plus wider coverage of the UK and
 * Singapore, where most users are: in Singapore that includes the private institutions
 * (SIM and its partner programmes, Kaplan, PSB) and the polytechnics. Each entry lists the email domains its students use; a
 * domain also covers its subdomains, so "ox.ac.uk" accepts "st-hughs.ox.ac.uk" as well.
 * Several universities give students a separate mail domain (u.nus.edu, uq.net.au,
 * studbocconi.it), which is why a domain list rather than a single domain.
 *
 * A student whose university is missing can choose "My university isn't listed", which
 * falls back to the generic academic-domain check in studentVerification.service.js.
 * Add an institution here rather than widening that check.
 */

const u = (id, name, country, domains) => ({ id, name, country, domains });

export const UNIVERSITIES = [
  // United Kingdom
  u("oxford", "University of Oxford", "United Kingdom", ["ox.ac.uk"]),
  u("cambridge", "University of Cambridge", "United Kingdom", ["cam.ac.uk"]),
  u("imperial", "Imperial College London", "United Kingdom", ["imperial.ac.uk", "ic.ac.uk"]),
  u("ucl", "University College London (UCL)", "United Kingdom", ["ucl.ac.uk"]),
  u("kcl", "King's College London", "United Kingdom", ["kcl.ac.uk"]),
  u("lse", "London School of Economics and Political Science", "United Kingdom", ["lse.ac.uk"]),
  u("edinburgh", "University of Edinburgh", "United Kingdom", ["ed.ac.uk"]),
  u("manchester", "University of Manchester", "United Kingdom", ["manchester.ac.uk"]),
  u("bristol", "University of Bristol", "United Kingdom", ["bristol.ac.uk"]),
  u("warwick", "University of Warwick", "United Kingdom", ["warwick.ac.uk"]),
  u("glasgow", "University of Glasgow", "United Kingdom", ["glasgow.ac.uk", "gla.ac.uk"]),
  u("durham", "Durham University", "United Kingdom", ["durham.ac.uk"]),
  u("birmingham", "University of Birmingham", "United Kingdom", ["bham.ac.uk", "birmingham.ac.uk"]),
  u("leeds", "University of Leeds", "United Kingdom", ["leeds.ac.uk"]),
  u("southampton", "University of Southampton", "United Kingdom", ["soton.ac.uk", "southampton.ac.uk"]),
  u("sheffield", "University of Sheffield", "United Kingdom", ["sheffield.ac.uk"]),
  u("st-andrews", "University of St Andrews", "United Kingdom", ["st-andrews.ac.uk"]),
  u("nottingham", "University of Nottingham", "United Kingdom", ["nottingham.ac.uk"]),
  u("qmul", "Queen Mary University of London", "United Kingdom", ["qmul.ac.uk"]),
  u("bath", "University of Bath", "United Kingdom", ["bath.ac.uk"]),
  u("exeter", "University of Exeter", "United Kingdom", ["exeter.ac.uk"]),
  u("lancaster", "Lancaster University", "United Kingdom", ["lancaster.ac.uk"]),
  u("york", "University of York", "United Kingdom", ["york.ac.uk"]),
  u("newcastle", "Newcastle University", "United Kingdom", ["ncl.ac.uk", "newcastle.ac.uk"]),
  u("liverpool", "University of Liverpool", "United Kingdom", ["liverpool.ac.uk", "liv.ac.uk"]),
  u("cardiff", "Cardiff University", "United Kingdom", ["cardiff.ac.uk"]),
  u("loughborough", "Loughborough University", "United Kingdom", ["lboro.ac.uk"]),
  u("rhul", "Royal Holloway, University of London", "United Kingdom", ["rhul.ac.uk"]),
  u("surrey", "University of Surrey", "United Kingdom", ["surrey.ac.uk"]),
  u("brunel", "Brunel University of London", "United Kingdom", ["brunel.ac.uk"]),
  u("city", "City St George's, University of London", "United Kingdom", ["city.ac.uk", "citystgeorges.ac.uk"]),
  u("reading", "University of Reading", "United Kingdom", ["reading.ac.uk"]),
  u("leicester", "University of Leicester", "United Kingdom", ["leicester.ac.uk", "le.ac.uk"]),
  u("sussex", "University of Sussex", "United Kingdom", ["sussex.ac.uk"]),
  u("qub", "Queen's University Belfast", "United Kingdom", ["qub.ac.uk"]),
  u("aberdeen", "University of Aberdeen", "United Kingdom", ["abdn.ac.uk"]),
  u("strathclyde", "University of Strathclyde", "United Kingdom", ["strath.ac.uk"]),
  u("kent", "University of Kent", "United Kingdom", ["kent.ac.uk"]),
  u("east-anglia", "University of East Anglia", "United Kingdom", ["uea.ac.uk"]),
  u("heriot-watt", "Heriot-Watt University", "United Kingdom", ["hw.ac.uk"]),

  // United States
  u("mit", "Massachusetts Institute of Technology (MIT)", "United States", ["mit.edu"]),
  u("stanford", "Stanford University", "United States", ["stanford.edu"]),
  u("harvard", "Harvard University", "United States", ["harvard.edu"]),
  u("caltech", "California Institute of Technology (Caltech)", "United States", ["caltech.edu"]),
  u("princeton", "Princeton University", "United States", ["princeton.edu"]),
  u("yale", "Yale University", "United States", ["yale.edu"]),
  u("uchicago", "University of Chicago", "United States", ["uchicago.edu"]),
  u("upenn", "University of Pennsylvania", "United States", ["upenn.edu"]),
  u("columbia", "Columbia University", "United States", ["columbia.edu"]),
  u("cornell", "Cornell University", "United States", ["cornell.edu"]),
  u("jhu", "Johns Hopkins University", "United States", ["jhu.edu", "jh.edu"]),
  u("berkeley", "University of California, Berkeley", "United States", ["berkeley.edu"]),
  u("ucla", "University of California, Los Angeles", "United States", ["ucla.edu"]),
  u("umich", "University of Michigan", "United States", ["umich.edu"]),
  u("nyu", "New York University", "United States", ["nyu.edu"]),
  u("cmu", "Carnegie Mellon University", "United States", ["cmu.edu"]),
  u("duke", "Duke University", "United States", ["duke.edu"]),
  u("northwestern", "Northwestern University", "United States", ["northwestern.edu"]),
  u("gatech", "Georgia Institute of Technology", "United States", ["gatech.edu"]),
  u("uiuc", "University of Illinois Urbana-Champaign", "United States", ["illinois.edu"]),
  u("uw", "University of Washington", "United States", ["uw.edu", "washington.edu"]),
  u("utexas", "University of Texas at Austin", "United States", ["utexas.edu"]),
  u("ucsd", "University of California, San Diego", "United States", ["ucsd.edu"]),
  u("brown", "Brown University", "United States", ["brown.edu"]),
  u("dartmouth", "Dartmouth College", "United States", ["dartmouth.edu"]),

  // Canada
  u("toronto", "University of Toronto", "Canada", ["utoronto.ca"]),
  u("mcgill", "McGill University", "Canada", ["mcgill.ca"]),
  u("ubc", "University of British Columbia", "Canada", ["ubc.ca"]),
  u("waterloo", "University of Waterloo", "Canada", ["uwaterloo.ca"]),

  // Europe
  u("eth", "ETH Zurich", "Switzerland", ["ethz.ch"]),
  u("epfl", "EPFL", "Switzerland", ["epfl.ch"]),
  u("uzh", "University of Zurich", "Switzerland", ["uzh.ch"]),
  u("tum", "Technical University of Munich", "Germany", ["tum.de", "mytum.de"]),
  u("lmu", "LMU Munich", "Germany", ["lmu.de", "uni-muenchen.de"]),
  u("heidelberg", "Heidelberg University", "Germany", ["uni-heidelberg.de"]),
  u("tu-berlin", "Technische Universität Berlin", "Germany", ["tu-berlin.de"]),
  u("hu-berlin", "Humboldt University of Berlin", "Germany", ["hu-berlin.de"]),
  u("fu-berlin", "Freie Universität Berlin", "Germany", ["fu-berlin.de"]),
  u("psl", "Université PSL", "France", ["psl.eu", "ens.psl.eu"]),
  u("ip-paris", "Institut Polytechnique de Paris", "France", ["ip-paris.fr", "polytechnique.edu"]),
  u("sorbonne", "Sorbonne University", "France", ["sorbonne-universite.fr"]),
  u("kuleuven", "KU Leuven", "Belgium", ["kuleuven.be"]),
  u("tudelft", "Delft University of Technology", "Netherlands", ["tudelft.nl"]),
  u("uva", "University of Amsterdam", "Netherlands", ["uva.nl"]),
  u("kth", "KTH Royal Institute of Technology", "Sweden", ["kth.se"]),
  u("karolinska", "Karolinska Institutet", "Sweden", ["ki.se"]),
  u("lund", "Lund University", "Sweden", ["lu.se"]),
  u("copenhagen", "University of Copenhagen", "Denmark", ["ku.dk"]),
  u("helsinki", "University of Helsinki", "Finland", ["helsinki.fi"]),
  u("oslo", "University of Oslo", "Norway", ["uio.no"]),
  u("tcd", "Trinity College Dublin", "Ireland", ["tcd.ie"]),
  u("ucd", "University College Dublin", "Ireland", ["ucd.ie", "ucdconnect.ie"]),
  u("polimi", "Politecnico di Milano", "Italy", ["polimi.it"]),
  u("bocconi", "Bocconi University", "Italy", ["unibocconi.it", "studbocconi.it"]),
  u("ub", "University of Barcelona", "Spain", ["ub.edu"]),

  // Singapore: autonomous universities
  u("nus", "National University of Singapore (NUS)", "Singapore", ["nus.edu.sg", "u.nus.edu"]),
  u("ntu", "Nanyang Technological University (NTU)", "Singapore", ["ntu.edu.sg"]),
  u("smu", "Singapore Management University (SMU)", "Singapore", ["smu.edu.sg"]),
  u("sutd", "Singapore University of Technology and Design (SUTD)", "Singapore", ["sutd.edu.sg"]),
  u("sit", "Singapore Institute of Technology (SIT)", "Singapore", ["singaporetech.edu.sg"]),
  u("suss", "Singapore University of Social Sciences (SUSS)", "Singapore", ["suss.edu.sg"]),
  u("uas", "University of the Arts Singapore (UAS)", "Singapore", ["uas.edu.sg"]),
  u("duke-nus", "Duke-NUS Medical School", "Singapore", ["duke-nus.edu.sg", "u.duke.nus.edu"]),

  // Singapore: SIM Global Education and its university partners, as listed at
  // sim.edu.sg/degrees-diplomas/sim-global-education/university-partners-sim-ge.
  // Students on a partner programme get a SIM address (name@mymail.sim.edu.sg) as well
  // as one from the awarding university, so either is accepted. The partner domains
  // cover their student subdomains: student.rmit.edu.au, student.bham.ac.uk,
  // uni.sydney.edu.au and so on.
  u("sim", "Singapore Institute of Management (SIM)", "Singapore", ["sim.edu.sg"]),
  u("sim-uow", "SIM - University of Wollongong (SIM-UOW)", "Singapore",
    ["sim.edu.sg", "uowmail.edu.au", "uow.edu.au"]),
  u("sim-uol", "SIM - University of London (SIM-UOL)", "Singapore", ["sim.edu.sg", "london.ac.uk"]),
  u("sim-rmit", "SIM - RMIT University (SIM-RMIT)", "Singapore", ["sim.edu.sg", "rmit.edu.au"]),
  u("sim-ub", "SIM - University at Buffalo (SIM-UB)", "Singapore", ["sim.edu.sg", "buffalo.edu"]),
  u("sim-monash", "SIM - Monash College", "Singapore",
    ["sim.edu.sg", "monashcollege.edu.au", "monash.edu"]),
  u("sim-usyd", "SIM - The University of Sydney", "Singapore", ["sim.edu.sg", "sydney.edu.au"]),
  u("sim-uob", "SIM - University of Birmingham", "Singapore", ["sim.edu.sg", "bham.ac.uk"]),
  u("sim-cardiff", "SIM - Cardiff University", "Singapore", ["sim.edu.sg", "cardiff.ac.uk"]),
  u("sim-stirling", "SIM - University of Stirling", "Singapore", ["sim.edu.sg", "stir.ac.uk"]),
  u("sim-warwick", "SIM - The University of Warwick", "Singapore", ["sim.edu.sg", "warwick.ac.uk"]),
  u("sim-ualberta", "SIM - University of Alberta", "Singapore", ["sim.edu.sg", "ualberta.ca"]),
  u("sim-gem", "SIM - Grenoble Ecole de Management (GEM)", "Singapore",
    ["sim.edu.sg", "grenoble-em.com"]),

  // Singapore: other private institutions and overseas campuses
  // Kaplan's student address domain is not published; these are its own domains.
  u("kaplan-sg", "Kaplan Singapore", "Singapore", ["kaplan.com.sg", "kaplan.com"]),
  u("psb", "PSB Academy", "Singapore", ["psb-academy.edu.sg"]),
  u("mdis", "Management Development Institute of Singapore (MDIS)", "Singapore", ["mdis.edu.sg"]),
  u("jcu-sg", "James Cook University Singapore", "Singapore", ["jcu.edu.sg", "jcu.edu.au"]),
  u("curtin-sg", "Curtin Singapore", "Singapore", ["curtin.edu.sg", "curtin.edu.au"]),
  u("amity-sg", "Amity Global Institute", "Singapore", ["amity.edu.sg"]),
  u("easb", "East Asia Institute of Management (EASB)", "Singapore", ["easb.edu.sg"]),
  u("digipen-sg", "DigiPen Institute of Technology Singapore", "Singapore", ["digipen.edu"]),
  u("insead", "INSEAD (Asia Campus)", "Singapore", ["insead.edu"]),
  u("essec-apac", "ESSEC Business School Asia-Pacific", "Singapore", ["essec.edu"]),
  u("spjain-sg", "S P Jain School of Global Management", "Singapore", ["spjain.org"]),
  u("lasalle", "LASALLE College of the Arts", "Singapore", ["lasalle.edu.sg"]),
  u("nafa", "Nanyang Academy of Fine Arts (NAFA)", "Singapore", ["nafa.edu.sg"]),

  // Singapore: polytechnics and ITE
  u("sp", "Singapore Polytechnic", "Singapore", ["sp.edu.sg"]),
  u("np", "Ngee Ann Polytechnic", "Singapore", ["np.edu.sg"]),
  u("tp", "Temasek Polytechnic", "Singapore", ["tp.edu.sg"]),
  u("nyp", "Nanyang Polytechnic", "Singapore", ["nyp.edu.sg"]),
  u("rp", "Republic Polytechnic", "Singapore", ["rp.edu.sg", "myrp.edu.sg"]),
  u("ite", "Institute of Technical Education (ITE)", "Singapore", ["ite.edu.sg"]),

  // Asia
  u("tsinghua", "Tsinghua University", "China", ["tsinghua.edu.cn"]),
  u("pku", "Peking University", "China", ["pku.edu.cn"]),
  u("fudan", "Fudan University", "China", ["fudan.edu.cn"]),
  u("zju", "Zhejiang University", "China", ["zju.edu.cn"]),
  u("sjtu", "Shanghai Jiao Tong University", "China", ["sjtu.edu.cn"]),
  u("hku", "The University of Hong Kong", "Hong Kong", ["hku.hk"]),
  u("hkust", "Hong Kong University of Science and Technology", "Hong Kong", ["ust.hk"]),
  u("cuhk", "The Chinese University of Hong Kong", "Hong Kong", ["cuhk.edu.hk"]),
  u("tokyo", "The University of Tokyo", "Japan", ["u-tokyo.ac.jp"]),
  u("kyoto", "Kyoto University", "Japan", ["kyoto-u.ac.jp"]),
  u("snu", "Seoul National University", "South Korea", ["snu.ac.kr"]),
  u("kaist", "KAIST", "South Korea", ["kaist.ac.kr"]),
  u("yonsei", "Yonsei University", "South Korea", ["yonsei.ac.kr"]),
  u("iitb", "Indian Institute of Technology Bombay", "India", ["iitb.ac.in"]),
  u("iitd", "Indian Institute of Technology Delhi", "India", ["iitd.ac.in"]),
  u("iisc", "Indian Institute of Science", "India", ["iisc.ac.in"]),

  // Oceania
  u("melbourne", "University of Melbourne", "Australia", ["unimelb.edu.au"]),
  u("sydney", "University of Sydney", "Australia", ["sydney.edu.au"]),
  u("unsw", "UNSW Sydney", "Australia", ["unsw.edu.au"]),
  u("anu", "Australian National University", "Australia", ["anu.edu.au"]),
  u("monash", "Monash University", "Australia", ["monash.edu"]),
  u("uq", "University of Queensland", "Australia", ["uq.edu.au", "uq.net.au"]),
  u("auckland", "University of Auckland", "New Zealand", ["auckland.ac.nz", "aucklanduni.ac.nz"]),

  // Middle East, Africa, Latin America
  u("technion", "Technion – Israel Institute of Technology", "Israel", ["technion.ac.il"]),
  u("tau", "Tel Aviv University", "Israel", ["tau.ac.il"]),
  u("kaust", "King Abdullah University of Science and Technology", "Saudi Arabia", ["kaust.edu.sa"]),
  u("uct", "University of Cape Town", "South Africa", ["uct.ac.za", "myuct.ac.za"]),
  u("usp", "University of São Paulo", "Brazil", ["usp.br"]),
  u("unam", "National Autonomous University of Mexico", "Mexico", ["unam.mx"]),
];

const byId = new Map(UNIVERSITIES.map((entry) => [entry.id, entry]));

export function findUniversity(id) {
  return id ? (byId.get(id) ?? null) : null;
}
