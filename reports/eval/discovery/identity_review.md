# City identity draft — review sheet

Drafted from GeoNames and Wikidata by `scripts/build_city_identity.py` (no workbook input). To approve an entry: check it here, edit `config/city_identity.yaml` if needed (move a name between `unverified_names`, `names` and `context_names`, fix a region), set the entry's `status: APPROVED` and fill `approval` at the top. Research uses approved entries only; until then a city is identified by its catalog name and country alone.

Names used for matching are confirmed by both sources or are Wikidata's official or native name. Unverified names are never used unless you promote one (add a `{source: person, id: <your name>}` source to it). Metropolitan areas are context only, never the city.

**Needs attention:** Bogotá, Colombia; Kyiv, Ukraine; Ho Chi Minh City, Viet Nam

## Luanda, Angola — DRAFT

- Region: Luanda Province (AO-LUA; term province; verified)
- Names used: Loanda (historical_spelling), São Paulo de Loanda (alternate), São Paulo de Luanda (alternate)
- Unverified (not used): São Paulo da Assunção de Loanda, St Paul of Loanda, St Paul of Luanda, St. Paul of Loanda, St. Paul of Luanda, Saint Paul of Loanda, Saint Paul of Luanda, Cidade de Luanda, São Paolo de Loanda, São Paulo da Assunção de Luanda, St Paul de Loanda, Saint Paul de Loanda
- Context (metro/urban areas): —
- Homonyms (4): Luanda (Bissau, GW), Loanda (Paraná, BR), Luanda (Vihiga County, KE), Luanda (Busia County, KE)

## Vienna, Austria — DRAFT

- Region: Vienna (AT-9; term state; verified)
- Names used: Wien (official_local, same as region), Beč (alternate, same as region), Bécs (alternate)
- Unverified (not used): —
- Context (metro/urban areas): Vienna-Bratislava metropolitan region
- Homonyms (4): Vienna (Virginia, US), Vienna (West Virginia, US), Vienna (Georgia, US), Vienna (Illinois, US)
- Note: the city is itself a first-level unit (Wikidata P300 on the city item)

## Belém, Brazil — DRAFT

- Region: Pará (BR-PA; term state; verified)
- Names used: Mairi (official_local), Santa Maria de Belém do Grão-Pará (alternate), Belém do Pará (alternate)
- Unverified (not used): Belen, Belén del Para, Belén de Pará, Pará
- Context (metro/urban areas): Greater Belém, Região Metropolitana de Belém
- Homonyms (11): Belem (São Paulo, BR), Belém de São Francisco (Pernambuco, BR), Mairi (Bahia, BR), Belém (Paraíba, BR), Belem (Bissau, GW), Belém (Lisbon, PT), Belém de Maria (Pernambuco, BR), Belém do Brejo do Cruz (Paraíba, BR) … +3
- Note: Wikidata item Q12829733 matched by name and country (its region chain reaches GeoNames admin1 3393129); the item carrying the GeoNames id (Q22060306) is a stub

## Brasília, Brazil — DRAFT

- Region: Federal District (BR-DF; term federal district; verified)
- Names used: —
- Unverified (not used): —
- Context (metro/urban areas): —
- Homonyms (1): Brasília de Minas (Minas Gerais, BR)

## Douala, Cameroon — DRAFT

- Region: Littoral (CM-LT; term region; verified)
- Names used: —
- Unverified (not used): Douala City, Duala, Doula
- Context (metro/urban areas): —
- Homonyms (0): —

## Toronto, Canada — DRAFT

- Region: Ontario (CA-ON; term province; verified)
- Names used: City of Toronto (official_local)
- Unverified (not used): The Six, T-O, The 416, Hogtown, ᑐᕌᓐᑐ
- Context (metro/urban areas): Greater Toronto Area, GTA, Greater Toronto, Toronto metropolitan area, Toronto metro area, Toronto area, Grand Toronto, Région métropolitaine de Toronto
- Homonyms (2): Toronto (New South Wales, AU), Toronto (Ohio, US)

## Santiago, Chile — DRAFT

- Region: Santiago Metropolitan Region (CL-RM; term region; verified)
- Names used: Santiago de Chile (official_local)
- Unverified (not used): Greater Santiago, Santiago del Nuevo Extremo, Santiago de la Nueva Extremadura, Gran Santiago, Santiago de Nueva Extremadura, Ciudad de Santiago, Ciudad de Santiago de Chile, Metrópolis de Santiago, Capital de Chile, La Capital de Chile, La Capital, CiudadSantiago
- Context (metro/urban areas): —
- Homonyms (40): Santiago de Leon de Caracas (Distrito Federal, VE), Santiago de Guayaquil (Guayas, EC), Santiago de Cali (Valle del Cauca Department, CO), Santiago de Querétaro (Querétaro, MX), Santiago de los Caballeros (Santiago Province, DO), Santiago de Guatimala (Guatemala, GT), Santiago de Cuba (Santiago de Cuba Province, CU), Santiago de los Caballeros de Merida (Mérida, VE) … +32

## Bogotá, Colombia — DRAFT

- Region: Bogota D.C. (no ISO code; term —; **NOT verified**)
- Names used: Santafé de Bogotá (historical_spelling)
- Unverified (not used): Santa Fé de Bogotá, Distrito Capital (Colombia)
- Context (metro/urban areas): —
- Homonyms (1): Bogota (New Jersey, US)
- Note: region not verified: no Wikidata P131 ancestor carries the GeoNames admin1 id 3688685

## Cali, Colombia — DRAFT

- Region: Valle del Cauca Department (CO-VAC; term department; verified)
- Names used: Santiago de Cali (official_local)
- Unverified (not used): Kali, La Sucursal del Cielo, La Sultana del Valle, Capital Mundial de la Salsa, Capital Americana del Deporte 2019
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: Wikidata item Q51103 matched by name and country (its region chain reaches GeoNames admin1 3666313); the item carrying the GeoNames id (Q23696223) is a stub

## Prague, Czech Republic — DRAFT

- Region: Prague (CZ-10; term region; verified)
- Names used: Praha (official_local, same as region), hlavní město Praha (official_local, same as region)
- Unverified (not used): City of Prague, okres Praha, okres Hlavní město Praha, kraj hlavní město Praha, kraj Praha, Hlavné mesto Praha, Stovežatá
- Context (metro/urban areas): —
- Homonyms (1): Prague (Oklahoma, US)
- Note: the city is itself a first-level unit (Wikidata P300 on the city item)

## Quito, Ecuador — DRAFT

- Region: Pichincha Province (EC-P; term province; verified)
- Names used: San Francisco de Quito (official_local)
- Unverified (not used): Kitu, Mitad del Mundo, Carita de Dios, Luz de América, Quitu
- Context (metro/urban areas): —
- Homonyms (0): —

## San Salvador, El Salvador — DRAFT

- Region: San Salvador Department (SV-SS; term department; verified)
- Names used: —
- Unverified (not used): —
- Context (metro/urban areas): Metropolitan Area of San Salvador, Área metropolitana de San Salvador, AMSS (El Salvador), Gran San Salvador, Gran Área Metropolitana de San Salvador, GAMSS (El Salvador)
- Homonyms (4): San Salvador de Jujuy (Jujuy, AR), San Salvador (Entre Rios, AR), San Salvador (Western Visayas, PH), San Salvador (Hidalgo, MX)

## Addis Ababa, Ethiopia — DRAFT

- Region: Addis Ababa (ET-AA; term region; verified)
- Names used: አዲስ አበባ (official_local, same as region), Finfinne (official_local, same as region), Tungga (official_local, same as region)
- Unverified (not used): ሸገር, Ādīs Ābeba, Addis Abeba, Addis Abbaba
- Context (metro/urban areas): —
- Homonyms (0): —

## Helsinki, Finland — DRAFT

- Region: Uusimaa (FI-18; term region; verified)
- Names used: Helsingin kaupunki (official_local), Helsingfors stad (official_local), Хельсинки (official_local), Helsingfors (official_local)
- Unverified (not used): Helsingia, Stadi, Hesa, Hki, Helsig
- Context (metro/urban areas): Helsinki capital region, Finnish capital region, capital region in Finland, pääkaupunkiseutu, PK-seutu, huvudstadsregionen, huvudstadsregionen (Finland), Finlands huvudstadsregion, uáivikaavpugkuávlu, Helsinki metropolitan area, The City Region of Helsinki, The Helsinki Region, Helsinki region, Greater Helsinki, Helsingin seutu … +3
- Homonyms (0): —

## Paris, France — DRAFT

- Region: Île-de-France (FR-IDF; term region; verified)
- Names used: Pariz (alternate)
- Unverified (not used): City of Light, City of Love, Lutetia, Ville-Lumière, Paname, Lutèce, Ville de l'Amour, FR-75, Pantruche, Ville de Paris
- Context (metro/urban areas): Inner ring of Paris, Petite Couronne
- Homonyms (9): Paris (Texas, US), Paris (Ontario, CA), Paris (Tennessee, US), Paris (Kentucky, US), Paris (Illinois, US), Pārīz (Kerman, IR), Paris (Maine, US), Paris (Arkansas, US) … +1

## Hamburg, Germany — DRAFT

- Region: Hamburg (DE-HH; term state; verified)
- Names used: Freie und Hansestadt Hamburg (official_local, same as region), Land Hamburg (official_local, same as region)
- Unverified (not used): Free and Hanseatic City of Hamburg, Hansestadt Hamburg, DE-HH, HH, FHH
- Context (metro/urban areas): Hamburg Metropolitan Region, Metropolregion Hamburg
- Homonyms (6): Hamburg (New York, US), Hamburg (Pennsylvania, US), Hamburg (New Jersey, US), Hamburg (Arkansas, US), Hamburg (Eastern Cape, ZA), Hamburg (Iowa, US)

## Kumasi, Ghana — DRAFT

- Region: Ashanti Region (GH-AH; term region; verified)
- Names used: Coomassie (alternate), Kumase (alternate)
- Unverified (not used): Comassie, Kumassi
- Context (metro/urban areas): —
- Homonyms (0): —

## Athínai (Athens), Greece — DRAFT

- Region: Attica Region (GR-I; term region; verified)
- Names used: Αθήνα (official_local), Ἀθῆναι (historical_spelling), Athènes (alternate)
- Unverified (not used): Athens city, Athenae, Atene, Afini, Athen, Athinia, Athína, Athenai
- Context (metro/urban areas): —
- Homonyms (10): Athens (Georgia, US), Athens (Ohio, US), Athens (Alabama, US), Athens (Tennessee, US), Athens (Texas, US), Athens (Pennsylvania, US), Athens (Illinois, US), Athens (New York, US) … +2

## Agra, India — DRAFT

- Region: Uttar Pradesh (IN-UP; term state; verified)
- Names used: आगरा (official_local)
- Unverified (not used): Agrah, ताज का शहर, আগ্রা, آگرا
- Context (metro/urban areas): —
- Homonyms (0): —

## Hyderabad, India — DRAFT

- Region: Telangana (IN-TS; term state; verified)
- Names used: హైదరాబాద్ (official_local), حیدر آباد‎ (official_local), హైదరాబాదు (official_local), حیدرآباد (official_local), हैदराबाद (alternate), হায়দরাবাদ (alternate)
- Unverified (not used): City of Pearls, Haidarābād, Hyd, मुत्याल नगर, Bhaganagar, Hyderabad AP
- Context (metro/urban areas): —
- Homonyms (1): Hyderabad (Sindh, PK)

## Jaipur, India — DRAFT

- Region: Rajasthan (IN-RJ; term state; verified)
- Names used: जयपुर (official_local), জয়পুর (alternate)
- Unverified (not used): Pink City, पिंक सिटी, গোলাপি শহর, Jeypore, Jayapur, Jainagar, Jaipur City
- Context (metro/urban areas): —
- Homonyms (0): —

## Kozhikode (Calicut), India — DRAFT

- Region: Kerala (IN-KL; term state; verified)
- Names used: कोष़िक्कोड (alternate)
- Unverified (not used): कोलिकोड, कोज़ीकोड, कालीकट, কালিকট, Kolikod, Kozhikkot, Kalicut, কোঝিকোড়
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: GeoNames id 1265873 is on several Wikidata items ['Q26787559', 'Q28729']; used Q28729 (most sitelinks)

## Pune (Poona), India — DRAFT

- Region: Maharashtra (IN-MH; term state; verified)
- Names used: पुणे (official_local), পুণে (alternate)
- Unverified (not used): Queen of the Deccan, Oxford of the East, Poonah, पूना, पूर्व का ऑक्सफ़ोर्ड, दक्कन की रानी, Puna
- Context (metro/urban areas): Pune Metropolitan Region, PMR, पुणे महानगर क्षेत्र
- Homonyms (0): —

## Bekasi, Indonesia — DRAFT

- Region: West Java (ID-JB; term province; verified)
- Names used: Kota Bekasi (official_local)
- Unverified (not used): Bekasi City, Kotamadya Daerah Tingkat II Bekasi
- Context (metro/urban areas): Jabodetabek, Greater Jakarta, Jakarta metropolitan area, Jabodetabekpunjur, Jakarta Raya, Kawasan metropolitan Jakarta, Jabodetabekjur, Jabotabek
- Homonyms (0): —

## Baghdad, Iraq — DRAFT

- Region: Baghdad Governorate (IQ-BG; term governorate; verified)
- Names used: بغداد (official_local, same as region), Bexda (alternate), Բաղդադ (alternate, same as region)
- Unverified (not used): Bagdat, مدينة السلام, بغداد (مدينة), بغداد الرشيد, Bagdad, Beghdad, بەغداد
- Context (metro/urban areas): —
- Homonyms (0): —

## Dublin, Ireland — DRAFT

- Region: Leinster (IE-L; term province; verified)
- Names used: Baile Átha Cliath (official_local), Dublin city (alternate)
- Unverified (not used): City of Dublin, Duibhlinn
- Context (metro/urban areas): —
- Homonyms (7): Dublin (California, US), Dublin (Ohio, US), Dublin (Georgia, US), Dublin (Texas, US), Dublin (Virginia, US), Dublin (Pennsylvania, US), Dublin (New Hampshire, US)

## Milan (Milano), Italy — DRAFT

- Region: Lombardy (IT-25; term region; verified)
- Names used: Mailand (alternate)
- Unverified (not used): Mediolanum
- Context (metro/urban areas): Milan metropolitan area, area metropolitana di Milano, Grande Milano, Milano – Busto Arsizio – Como metropolitan area
- Homonyms (9): Milan (Tennessee, US), Milán (Caquetá, CO), Milan (Michigan, US), Milan (Illinois, US), Milan (New Mexico, US), Milan (Indiana, US), Milan (Missouri, US), Milan (New Hampshire, US) … +1
- Note: GeoNames id 3173435 is on several Wikidata items ['Q490', 'Q49295412']; used Q490 (most sitelinks)

## Rome, Italy — DRAFT

- Region: Lazio (IT-62; term region; verified)
- Names used: Roma (official_local), Rom (alternate)
- Unverified (not used): The Eternal City, City of Seven Hills, Eternal City, The City of Marble, City of Marble, The Capital of the World, Capital of the World, Urbe, Città eterna, Caput Mundi, Città di Roma, RM, Città dei sette colli, Die Ewige Stadt, La ville éternelle … +2
- Context (metro/urban areas): —
- Homonyms (9): Rome (Georgia, US), Rome (New York, US), Roma (Maseru District, LS), Roma (Texas, US), Roma (Queensland, AU), Roma (Botoșani County, RO), Rome (Wisconsin, US), Rome (Illinois, US) … +1

## Sapporo, Japan — DRAFT

- Region: Hokkaido (JP-01; term prefecture; verified)
- Names used: 札幌市 (official_local), sat-poro-pet (official_local), 札幌 (alternate)
- Unverified (not used): 札幌区, 道都, サツホロ, シヤツホロ, サッ・ポロ・ペッ, Sapporo-shi, さっぽろし, Sapporoshi, さっぽろ, サッポロ, サッポロシ
- Context (metro/urban areas): Sapporo metropolitan area, Greater Sapporo, Sapporo M.M.A., Sapporo Major Metropolitan Area, 札幌都市圏, 札幌圏, 札幌広域圏, さっぽろ連携中枢都市圏
- Homonyms (0): —

## Sendai, Japan — DRAFT

- Region: Miyagi Prefecture (JP-04; term prefecture; verified)
- Names used: 仙台市 (official_local)
- Unverified (not used): 仙都, 杜の都, 仙臺, 千代, 先代, 僊臺, 学都仙台, 楽都仙台, せんだいし, 仙台, せんだい, センダイ, センダイシ, Sendaishi, Sendai-shi
- Context (metro/urban areas): Sendai metropolitan area, Greater Sendai, 仙台都市圏, 仙台圏
- Homonyms (1): Sendai (Kagoshima, JP)

## Nairobi, Kenya — DRAFT

- Region: Nairobi City County (KE-30; term county; verified)
- Names used: —
- Unverified (not used): Kenya's capital, Kenyan capital, Kenya capital city, Kenya capital, Mji mkuu wa Kenya, Nairoba
- Context (metro/urban areas): —
- Homonyms (0): —

## Antananarivo, Madagascar — DRAFT

- Region: Analamanga (no ISO code; term region; verified)
- Names used: Tananarive (official_local)
- Unverified (not used): Iarivo, Analamanga, Antananarivu
- Context (metro/urban areas): —
- Homonyms (0): —

## Kuala Lumpur, Malaysia — DRAFT

- Region: Kuala Lumpur (MY-14; term federal territory; verified)
- Names used: KL (abbreviation), Federal Territory of Kuala Lumpur (alternate, same as region), Wilayah Persekutuan Kuala Lumpur (alternate, same as region), 吉隆坡 (alternate)
- Unverified (not used): K.L., Wilayah Persekutuan, WPKL, Kuala Lumpor
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: GeoNames id 1735161 is on several Wikidata items ['Q1865', 'Q49755134']; used Q1865 (most sitelinks)
- Note: the city is itself a first-level unit (Wikidata P300 on the city item)

## Guadalajara, Mexico — DRAFT

- Region: Jalisco (MX-JAL; term state; verified)
- Names used: Γουαδαλαχάρα (Μεξικό) (official_local), 과달라하라 (멕시코) (official_local), 瓜达拉哈拉 (墨西哥) (official_local), Гвадалахара (official_local), Quadalaxara (official_local), グアダラハラ (メキシコ) (official_local), גוודלחרה (מקסיקו) (official_local), გვადალახარა (official_local), غوادالاخارا (official_local), กวาดาลาฮารา (ประเทศเม็กซิโก) (official_local), Atemaxac (official_local)
- Unverified (not used): GDL, La Perla Tapatía, Ciudad de las rosas
- Context (metro/urban areas): Guadalajara metropolitan area, GMA, Zona Metropolitana de Guadalajara, ZMG
- Homonyms (2): Guadalajara de Buga (Valle del Cauca Department, CO), Guadalajara (Castille-La Mancha, ES)

## Puebla, Mexico — DRAFT

- Region: Puebla (MX-PUE; term state; verified)
- Names used: Heroica Puebla de Zaragoza (official_local), Puebla de Zaragoza (alternate), Puebla City (alternate), Puebla de los Ángeles (alternate)
- Unverified (not used): Angelópolis, Ciudad de Puebla
- Context (metro/urban areas): —
- Homonyms (10): Puebla (Baja California, MX), Puebla de la Calzada (Extremadura, ES), Puebla de Alfindén (Aragon, ES), Puebla de Guzmán (Andalusia, ES), Puebla de Sancho Pérez (Extremadura, ES), Puebla de Obando (Extremadura, ES), Puebla de Sanabria (Castille and León, ES), Puebla (Chiapas, MX) … +2

## Amsterdam, Netherlands — DRAFT

- Region: North Holland (NL-NH; term province; verified)
- Names used: —
- Unverified (not used): Mokum, A'dam, Damsko, Asd, Mokum Alef, AMS, Aemsterdam, Amstardām, Âmesterdâm, Aemstelredamme, Āmǔsītèdān
- Context (metro/urban areas): —
- Homonyms (1): Amsterdam (New York, US)

## Rotterdam, Netherlands — DRAFT

- Region: South Holland (NL-ZH; term province; verified)
- Names used: —
- Unverified (not used): R'dam, Roffa, Rotjeknor, Mokum Reisj, Rotjeknar
- Context (metro/urban areas): —
- Homonyms (1): Rotterdam (New York, US)

## Kano, Nigeria — DRAFT

- Region: Kano State (NG-KN; term state; verified)
- Names used: —
- Unverified (not used): birnin Kano, Cano
- Context (metro/urban areas): —
- Homonyms (1): Kanō (Osaka, JP)

## Lisbon, Portugal — DRAFT

- Region: Lisbon (PT-11; term district; verified)
- Names used: Lisboa (official_local, same as region)
- Unverified (not used): Município de Lisboa, Lisboua, Olisipo, Felicitas Julia
- Context (metro/urban areas): —
- Homonyms (6): Lisbon (Maine, US), Lisbon (Connecticut, US), Lisbon (Ohio, US), Lisbon (Ohio, US), Lisbon (Iowa, US), Lisbon (North Dakota, US)

## Bucharest, Romania — DRAFT

- Region: Bucharest (RO-B; term —; verified)
- Names used: București (official_local, same as region), Bukarest (alternate)
- Unverified (not used): Little Paris, Paris of the East, Municipiul București, Bucureștiul, Micul Paris, Bucureștii, Bucuresci, Bucarest
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: the city is itself a first-level unit (Wikidata P300 on the city item)

## Kigali, Rwanda — DRAFT

- Region: Kigali (RW-01; term province; verified)
- Names used: Umujyi wa Kigali (official_local, same as region), City of Kigali (official_local, same as region), Ville de Kigali (official_local, same as region)
- Unverified (not used): Kigali City, Kigali Ville, Kigale
- Context (metro/urban areas): —
- Homonyms (0): —

## Dakar, Senegal — DRAFT

- Region: Dakar (SN-DK; term region; verified)
- Names used: Ville de Dakar (official_local), Ndakaaru (official_local)
- Unverified (not used): —
- Context (metro/urban areas): —
- Homonyms (0): —

## Busan, South Korea — DRAFT

- Region: Busan (KR-26; term metropolitan city; verified)
- Names used: 부산 (official_local, same as region), Fuzan (alternate), Pusan (alternate, same as region), 부산광역시 (alternate, same as region), 부산시 (alternate, same as region)
- Unverified (not used): Busan Metropolitan City, Pusan Metropolitan City, Busan-si, Tsau-liang-hai, Fusan, Fousan
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: GeoNames id 1838524 is on several Wikidata items ['Q16520', 'Q49280510']; used Q16520 (most sitelinks)

## Barcelona, Spain — DRAFT

- Region: Catalonia (ES-CT; term autonomous community; verified)
- Names used: —
- Unverified (not used): B'na, Barcelona City, City of Barcelona, Barna, Ciudad condal, Barcino, Ciutat Comtal, Rosa de Foc, Cap i Casal, Can Fanga, Cidade condal, Cidade de Barcelona, la Ciudad Condal
- Context (metro/urban areas): Barcelona metropolitan area, Àrea Metropolitana de Barcelona, AMB
- Homonyms (6): Barcelona (Anzoátegui, VE), Barcelona (Rio Grande do Norte, BR), Barcelona (Caraga, PH), Barcelona (Bicol Region, PH), Barcelona (Bicol Region, PH), Barcelona (Zamboanga Peninsula, PH)
- Note: Wikidata item Q1492 matched by name and country (its region chain reaches GeoNames admin1 3336901); the item carrying the GeoNames id (None) is a stub

## Madrid, Spain — DRAFT

- Region: Community of Madrid (ES-MD; term autonomous community; verified)
- Names used: —
- Unverified (not used): Magerit, La Villa y Corte de Madrid
- Context (metro/urban areas): Madrid Metropolitan Area, Madrid Metropolitan Area Agricultural Comarca, Área metropolitana de Madrid, Comarca agrícola del Área Metropolitana de Madrid
- Homonyms (4): Madrid (Cundinamarca, CO), Madrid (Colima, MX), Madrid (Caraga, PH), Madrid (Iowa, US)
- Note: Wikidata item Q2807 matched by name and country (its region chain reaches GeoNames admin1 3117732); the item carrying the GeoNames id (Q116170766) is a stub

## Stockholm, Sweden — DRAFT

- Region: Stockholm County (SE-AB; term county; verified)
- Names used: Stockholbma (alternate)
- Unverified (not used): Sthlm, Nordens Venedig, Stuehkie, Tukholma
- Context (metro/urban areas): —
- Homonyms (0): —

## Ankara, Turkey — DRAFT

- Region: Ankara Province (TR-06; term province; verified)
- Names used: آنقره (official_local), Enqere (alternate), Anqara (alternate, same as region)
- Unverified (not used): Engüri, Ankura, Ankyra, Anguriyah
- Context (metro/urban areas): —
- Homonyms (0): —

## Kyiv, Ukraine — DRAFT

- Region: Kyiv City (no ISO code; term —; **NOT verified**)
- Names used: Київ (official_local), Кыив (official_local), Киев (historical_spelling)
- Unverified (not used): Kyjiw, Kyiw, Kief, Kieff, Kiew, Kioff, Kiof, Kiowia, Kiovia, Kiow, Kiou, Kyїv, Kiyev, Kiev, Киевом … +1
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: region not verified: no Wikidata P131 ancestor carries the GeoNames admin1 id 703447

## London, United Kingdom — DRAFT

- Region: England (GB-ENG; term country; verified)
- Names used: Llundain (alternate), Lunnainn (alternate)
- Unverified (not used): London UK, London U.K., Londinium, Loñ, Lundenwic, Londinio, Londini, Londiniensium, Augusta, Trinovantum, Kaerlud, Karelundein, Lunden, Big Smoke, the Big Smoke … +9
- Context (metro/urban areas): —
- Homonyms (5): London (Ontario, CA), London (Ohio, US), London (Kentucky, US), London (California, US), London (Arkansas, US)

## Chicago, United States of America — DRAFT

- Region: Illinois (US-IL; term state; verified)
- Names used: City of Chicago (official_local), The Windy City (alternate)
- Unverified (not used): City of Broad Shoulders, Chi-Town, City by the Lake, The Second City, Chi-Raq, Chitown, The Queen of the West, La Reina de Occidente, Ciudad Junto al Lago, Ciudad de Chicago, La Segunda Ciudad, Ciudad de Hombros Anchos, La Ciudad Ventosa, Chicago (Illinois), Kikako … +1
- Context (metro/urban areas): Chicago metropolitan area, Greater Chicago, Chicagoland, Chicago–Naperville–Elgin metropolitan statistical area, Área metropolitana de Chicago, Área metropolitana de Chicago-Joliet-Naperville
- Homonyms (0): —

## Houston, United States of America — DRAFT

- Region: Texas (US-TX; term state; verified)
- Names used: —
- Unverified (not used): Space City, H-Town, City of Houston, USHOU, Houston TX, Petro metro, Houston (Texas), Ciudad de Houston, Ciudad Espacial, Houston City
- Context (metro/urban areas): Greater Houston, Houston metropolitan area, Houston area, Houston metro area, Houston–The Woodlands–Sugar Land-Galveston, Houston MSA, Gran Houston
- Homonyms (6): Houston (Scotland, GB), Houston (Mississippi, US), Houston (British Columbia, CA), Houston (Alaska, US), Houston (Missouri, US), Houston (Pennsylvania, US)

## New York City, United States of America — DRAFT

- Region: New York (US-NY; term state; verified)
- Names used: New York (official_local, same as region), City of New York (official_local), Nueva York (alternate, same as region)
- Unverified (not used): the five boroughs, NY City, Capital of the World, New York City (NYC), New York (city), Center of the Universe, New York (NY), Empire City, La Gran Manzana, NY Ciudad, Capital del Mundo, Ciudad de Nueva York (NYC), La Ciudad Tan Bonita Que La Nombraron Dos Veces, Los Cinco Distritos, Ciudad de Nueva York … +18
- Context (metro/urban areas): New York metropolitan area, Greater New York, Tri-state area, New York-Newark-Jersey City metropolitan statistical area, New York-Newark-Jersey City metropolitan area, New York-Newark-Jersey City metro area, Área metropolitana de Nueva York
- Homonyms (0): —

## Providence, United States of America — DRAFT

- Region: Rhode Island (US-RI; term state; verified)
- Names used: —
- Unverified (not used): Providence (Rhode Island)
- Context (metro/urban areas): Providence–Warwick metropolitan NECTA
- Homonyms (4): Providence (Utah, US), Providence (Kentucky, US), Providence (Moka, MU), Providence (Kentucky, US)

## Sacramento, United States of America — DRAFT

- Region: California (US-CA; term state; verified)
- Names used: Kakalameko (alternate)
- Unverified (not used): City of Sacramento, Sac-Town, Sac, The Indomitable City, City of Trees, The Big Tomato, The Capitol City, Camellia City, River City, La Ciudad Indomable, Sacramento (California), La Ciudad Capitolio, El Gran Tomate, Ciudad De Los Árboles, Ciudad De Camelia … +2
- Context (metro/urban areas): Sacramento metropolitan area, Greater Sacramento, Área metropolitana de Sacramento, Área metropolitana de Sacramento--Arden-Arcade--Roseville
- Homonyms (2): Sacramento (Minas Gerais, BR), Sacramento (Coahuila, MX)

## Ho Chi Minh City, Viet Nam — DRAFT

- Region: Ho Chi Minh City (HCMC) (no ISO code; term —; **NOT verified**)
- Names used: Thành phố Hồ Chí Minh (official_local), ក្រុងព្រៃនគរ (official_local), Sài Gòn (historical_spelling), Saïgon (historical_spelling), Gia Định (historical_spelling), TPHCM (abbreviation), Hô Chi Minh-Ville (alternate)
- Unverified (not used): Ho Chi Minh, HCMC, Hôchiminh Ville, Prey Nokor, HCM City, HCM, Sài Thành, Tp.HCM, Tp. HCM, TP HCM, Chợ Lớn, HCMV, T.P. Hồ Chí Minh, TP Ho Chi Minh
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: region not verified: no Wikidata P131 ancestor carries the GeoNames admin1 id 13494358

## Hà Noi, Viet Nam — DRAFT

- Region: Hanoi (VN-HN; term city; verified)
- Names used: Hanoi (alternate, same as region), Thang Long (historical_spelling)
- Unverified (not used): Dong Do, Tonkin, Đại La, Bắc Thành, Hà Thành, Đông Đô, Thành phố Hà Nội, Keicho, Kecho, Kẻ Chợ
- Context (metro/urban areas): —
- Homonyms (0): —
- Note: the city is itself a first-level unit (Wikidata P300 on the city item)
