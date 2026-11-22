"""Answer customers in the language they wrote in (live verdict `wrong_language`, rounds 70 FR
and 71 DE: "I could not follow it in this language, so we will not go ahead").

No model: the customer replies in replies.py are fixed sentences, so each line is matched and
rewritten from a phrase table. Values (amounts, weights, codes, names, dates) are copied
verbatim from the English line. A line without a table entry stays English, and if any number
of the English body is missing afterwards, the English body is sent unchanged.
"""

import re

_WORDS = {
    "en": "the and you your please could with for our will would this that have from are thank",
    "de": "der die das und wir sie ihr ihre ihnen bitte mit für von nach ist ein eine einen "
    "unsere sendung gewicht abholung grüße vielen dank",
    "fr": "le la les et nous vous votre vos pour avec une des du est merci envoi poids bonjour "
    "pouvez",
    "es": "el los las y nosotros usted su sus para con una del es gracias envío peso hola "
    "podrían",
    "it": "il lo gli e noi vostro vostra per con una del è grazie spedizione peso buongiorno "
    "potete",
    "nl": "het een en wij u uw voor met van is bedankt zending gewicht graag",
}
_SETS = {lang: frozenset(words.split()) for lang, words in _WORDS.items()}
_WORD = re.compile(r"[^\W\d_]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def detect(text: str) -> str:
    """Language code of a customer mail: "en" unless another language clearly dominates."""
    words = _WORD.findall((text or "").lower())
    score = {lang: sum(w in found for w in words) for lang, found in _SETS.items()}
    best = max((lang for lang in score if lang != "en"), key=score.get)
    return best if score[best] >= 4 and score[best] > 2 * score["en"] else "en"


_R = r"(?P<route>(?: from \S+ to \S+| from \S+| to \S+)?)"
_ROUTE = {  # (from X to Y, from X, to Y)
    "de": (" von {o} nach {d}", " ab {o}", " nach {d}"),
    "fr": (" de {o} à {d}", " au départ de {o}", " vers {d}"),
    "es": (" de {o} a {d}", " desde {o}", " a {d}"),
    "it": (" da {o} a {d}", " da {o}", " per {d}"),
    "nl": (" van {o} naar {d}", " vanaf {o}", " naar {d}"),
}
_AND = {"de": "und", "fr": "et", "es": "y", "it": "e", "nl": "en"}
LANGUAGES = frozenset(_ROUTE)  # the languages a reply can be written in besides English
_TERMS = {  # longest first; items inside sentences (charges, missing facts, documents, problems)
    "the total volume in cbm (or the dimensions and number of pieces)": {
        "de": "das Gesamtvolumen in cbm (oder die Abmessungen und die Anzahl der Packstücke)",
        "fr": "le volume total en cbm (ou les dimensions et le nombre de colis)",
        "es": "el volumen total en cbm (o las dimensiones y el número de bultos)",
        "it": "il volume totale in cbm (o le dimensioni e il numero di colli)",
        "nl": "het totale volume in cbm (of de afmetingen en het aantal colli)",
    },
    "the Shipper's Declaration for Dangerous Goods (DGD)": {
        "de": "die Gefahrgutanmeldung des Versenders (Shipper's Declaration for Dangerous Goods, "
        "DGD)",
        "fr": "la déclaration de l'expéditeur pour marchandises dangereuses (DGD)",
        "es": "la declaración del expedidor de mercancías peligrosas (DGD)",
        "it": "la dichiarazione del mittente per merci pericolose (DGD)",
        "nl": "de verklaring van de afzender voor gevaarlijke goederen (DGD)",
    },
    "the postcode or zone we should collect from": {
        "de": "die Postleitzahl oder Zone, in der wir abholen sollen",
        "fr": "le code postal ou la zone d'enlèvement",
        "es": "el código postal o la zona de recogida",
        "it": "il codice postale o la zona di ritiro",
        "nl": "de postcode of zone waar wij moeten ophalen",
    },
    "it names a different price from the one I quoted": {
        "de": "darin ein anderer Preis als in meinem Angebot steht",
        "fr": "elle mentionne un prix différent de celui de mon offre",
        "es": "menciona un precio distinto al de mi oferta",
        "it": "indica un prezzo diverso da quello della mia offerta",
        "nl": "er een andere prijs in staat dan in mijn offerte",
    },
    "it names a different route from the one I quoted": {
        "de": "darin eine andere Strecke als in meinem Angebot steht",
        "fr": "elle mentionne un trajet différent de celui de mon offre",
        "es": "menciona una ruta distinta a la de mi oferta",
        "it": "indica una tratta diversa da quella della mia offerta",
        "nl": "er een andere route in staat dan in mijn offerte",
    },
    "it asks for a different service from the one I quoted": {
        "de": "darin ein anderer Service als in meinem Angebot gewünscht wird",
        "fr": "elle demande un service différent de celui de mon offre",
        "es": "pide un servicio distinto al de mi oferta",
        "it": "richiede un servizio diverso da quello della mia offerta",
        "nl": "u om een andere service vraagt dan in mijn offerte",
    },
    "it adds a condition or asks me to wait": {
        "de": "sie eine Bedingung enthält oder mich bittet zu warten",
        "fr": "elle ajoute une condition ou me demande d'attendre",
        "es": "añade una condición o me pide esperar",
        "it": "aggiunge una condizione o mi chiede di aspettare",
        "nl": "er een voorwaarde in staat of u mij vraagt te wachten",
    },
    "the departure airport": {
        "de": "den Abflughafen",
        "fr": "l'aéroport de départ",
        "es": "el aeropuerto de salida",
        "it": "l'aeroporto di partenza",
        "nl": "de luchthaven van vertrek",
    },
    "the destination airport": {
        "de": "den Zielflughafen",
        "fr": "l'aéroport de destination",
        "es": "el aeropuerto de destino",
        "it": "l'aeroporto di destinazione",
        "nl": "de luchthaven van bestemming",
    },
    "the gross weight in kg": {
        "de": "das Bruttogewicht in kg",
        "fr": "le poids brut en kg",
        "es": "el peso bruto en kg",
        "it": "il peso lordo in kg",
        "nl": "het brutogewicht in kg",
    },
    "the safety data sheet (MSDS)": {
        "de": "das Sicherheitsdatenblatt (MSDS)",
        "fr": "la fiche de données de sécurité (MSDS)",
        "es": "la ficha de datos de seguridad (MSDS)",
        "it": "la scheda di dati di sicurezza (MSDS)",
        "nl": "het veiligheidsinformatieblad (MSDS)",
    },
    "air all in": {
        "de": "Luftfracht all-in",
        "fr": "fret aérien tout compris",
        "es": "flete aéreo todo incluido",
        "it": "nolo aereo tutto incluso",
        "nl": "luchtvracht all-in",
    },
    "air freight": {
        "de": "Luftfracht",
        "fr": "fret aérien",
        "es": "flete aéreo",
        "it": "nolo aereo",
        "nl": "luchtvracht",
    },
    "security": {
        "de": "Sicherheitszuschlag",
        "fr": "surcharge sûreté",
        "es": "recargo de seguridad",
        "it": "supplemento sicurezza",
        "nl": "veiligheidstoeslag",
    },
    "pickup": {
        "de": "Abholung",
        "fr": "enlèvement",
        "es": "recogida",
        "it": "ritiro",
        "nl": "ophalen",
    },
    "fuel": {
        "de": "Treibstoffzuschlag",
        "fr": "surcharge carburant",
        "es": "recargo de combustible",
        "it": "supplemento carburante",
        "nl": "brandstoftoeslag",
    },
}

# English line (replies.py) -> the same line per language. {route} and {terms} are filled in.
_LINES = [
    (
        r"Hello (?P<name>.+),",
        {"de": "Guten Tag {name},", "fr": "Bonjour {name},", "es": "Hola {name},",
         "it": "Buongiorno {name},", "nl": "Beste {name},"},
    ),
    (
        r"Hello,",
        {"de": "Guten Tag,", "fr": "Bonjour,", "es": "Hola,", "it": "Buongiorno,",
         "nl": "Goedendag,"},
    ),
    (
        r"Best regards,",
        {"de": "Mit freundlichen Grüßen", "fr": "Cordialement,", "es": "Saludos cordiales,",
         "it": "Cordiali saluti,", "nl": "Met vriendelijke groet,"},
    ),
    (
        r"Thank you for your request\. Here is our offer for (?P<w>\S+) kg" + _R + ":",
        {"de": "Vielen Dank für Ihre Anfrage. Hier ist unser Angebot für {w} kg{route}:",
         "fr": "Merci pour votre demande. Voici notre offre pour {w} kg{route} :",
         "es": "Gracias por su solicitud. Esta es nuestra oferta para {w} kg{route}:",
         "it": "Grazie per la sua richiesta. Ecco la nostra offerta per {w} kg{route}:",
         "nl": "Hartelijk dank voor uw aanvraag. Hier is ons aanbod voor {w} kg{route}:"},
    ),
    (
        r"Total: (?P<cur>\S+) (?P<total>\S+) all-in",
        {"de": "Gesamtpreis: {cur} {total}, alles inklusive",
         "fr": "Total : {cur} {total}, tout compris",
         "es": "Total: {cur} {total}, todo incluido",
         "it": "Totale: {cur} {total}, tutto incluso",
         "nl": "Totaal: {cur} {total}, alles inbegrepen"},
    ),
    (
        r"Carrier: (?P<carrier>.+), transit (?P<days>\d+) days",
        {"de": "Fluggesellschaft: {carrier}, Transitzeit {days} Tage",
         "fr": "Compagnie aérienne : {carrier}, transit {days} jours",
         "es": "Aerolínea: {carrier}, tránsito {days} días",
         "it": "Compagnia aerea: {carrier}, transito {days} giorni",
         "nl": "Luchtvaartmaatschappij: {carrier}, transittijd {days} dagen"},
    ),
    (
        r"Carrier: (?P<carrier>.+), transit time not confirmed",
        {"de": "Fluggesellschaft: {carrier}, Transitzeit noch nicht bestätigt",
         "fr": "Compagnie aérienne : {carrier}, délai de transit non confirmé",
         "es": "Aerolínea: {carrier}, tiempo de tránsito sin confirmar",
         "it": "Compagnia aerea: {carrier}, tempo di transito non confermato",
         "nl": "Luchtvaartmaatschappij: {carrier}, transittijd nog niet bevestigd"},
    ),
    (
        r"Included: (?P<terms>.+)",
        {"de": "Enthalten: {terms}", "fr": "Inclus : {terms}", "es": "Incluido: {terms}",
         "it": "Incluso: {terms}", "nl": "Inbegrepen: {terms}"},
    ),
    (
        r"Pickup: from zone (?P<zone>\S+) \(trucker offer (?P<ref>[^)]+)\)",
        {"de": "Abholung: ab Zone {zone} (Angebot des Spediteurs {ref})",
         "fr": "Enlèvement : depuis la zone {zone} (offre du transporteur {ref})",
         "es": "Recogida: desde la zona {zone} (oferta del transportista {ref})",
         "it": "Ritiro: dalla zona {zone} (offerta del trasportatore {ref})",
         "nl": "Ophalen: vanaf zone {zone} (aanbod vervoerder {ref})"},
    ),
    (
        r"Pickup: from zone (?P<zone>\S+)",
        {"de": "Abholung: ab Zone {zone}", "fr": "Enlèvement : depuis la zone {zone}",
         "es": "Recogida: desde la zona {zone}", "it": "Ritiro: dalla zona {zone}",
         "nl": "Ophalen: vanaf zone {zone}"},
    ),
    (
        r"Valid until: (?P<when>.+)",
        {"de": "Gültig bis: {when}", "fr": "Valable jusqu'au : {when}",
         "es": "Válido hasta: {when}", "it": "Valido fino al: {when}", "nl": "Geldig tot: {when}"},
    ),
    (
        r"To book, reply to this email and confirm that you accept\.",
        {"de": "Zur Buchung antworten Sie bitte auf diese E-Mail und bestätigen Sie, dass Sie "
         "das Angebot annehmen.",
         "fr": "Pour réserver, répondez à cet e-mail en confirmant que vous acceptez l'offre.",
         "es": "Para reservar, responda a este correo confirmando que acepta la oferta.",
         "it": "Per prenotare, risponda a questa e-mail confermando di accettare l'offerta.",
         "nl": "Om te boeken, beantwoord deze e-mail en bevestig dat u het aanbod accepteert."},
    ),
    (
        r"The offer is attached as a PDF\.",
        {"de": "Das Angebot finden Sie zusätzlich als PDF im Anhang.",
         "fr": "Vous trouverez l'offre en pièce jointe au format PDF.",
         "es": "Encontrará la oferta adjunta en formato PDF.",
         "it": "Trova l'offerta in allegato in formato PDF.",
         "nl": "U vindt het aanbod als pdf in de bijlage."},
    ),
    (
        r"Thank you for your request" + _R + r"\.",
        {"de": "Vielen Dank für Ihre Anfrage{route}.", "fr": "Merci pour votre demande{route}.",
         "es": "Gracias por su solicitud{route}.", "it": "Grazie per la sua richiesta{route}.",
         "nl": "Hartelijk dank voor uw aanvraag{route}."},
    ),
    (
        r"To price it I still need (?P<terms>.+?)\.(?P<note>( \(.*\))?)",
        {"de": "Für den Preis benötige ich noch {terms}.{note}",
         "fr": "Pour établir le prix, il me faut encore {terms}.{note}",
         "es": "Para calcular el precio todavía necesito {terms}.{note}",
         "it": "Per calcolare il prezzo mi servono ancora {terms}.{note}",
         "nl": "Om de prijs te berekenen heb ik nog {terms} nodig.{note}"},
    ),
    (
        r"As soon as I have that, I will send you the quote\.",
        {"de": "Sobald ich diese Angaben habe, sende ich Ihnen das Angebot.",
         "fr": "Dès que j'ai ces informations, je vous envoie l'offre.",
         "es": "En cuanto tenga esos datos, le enviaré la oferta.",
         "it": "Appena avrò questi dati, le invierò l'offerta.",
         "nl": "Zodra ik dat heb, stuur ik u de offerte."},
    ),
    (
        r"Your message mentions (?P<signals>.+), which ships as dangerous goods\. "
        r"Before I can quote, I need (?P<terms>.+)\.",
        {"de": "Ihre Nachricht erwähnt {signals}; das wird als Gefahrgut versendet. Bevor ich "
         "ein Angebot erstellen kann, benötige ich {terms}.",
         "fr": "Votre message mentionne {signals}, qui voyage en marchandises dangereuses. "
         "Avant de pouvoir vous faire une offre, j'ai besoin de {terms}.",
         "es": "Su mensaje menciona {signals}, que se envía como mercancía peligrosa. Antes de "
         "poder cotizar, necesito {terms}.",
         "it": "Il suo messaggio menziona {signals}, che viaggia come merce pericolosa. Prima "
         "di poter quotare mi servono {terms}.",
         "nl": "Uw bericht vermeldt {signals}; dat wordt als gevaarlijke goederen verzonden. "
         "Voordat ik een prijs kan geven, heb ik {terms} nodig."},
    ),
    (
        r"Please reply to this email with the documents attached and I will continue right "
        r"away\.",
        {"de": "Bitte antworten Sie auf diese E-Mail mit den Dokumenten im Anhang, dann mache "
         "ich sofort weiter.",
         "fr": "Merci de répondre à cet e-mail avec les documents en pièce jointe, et je "
         "poursuis immédiatement.",
         "es": "Responda a este correo con los documentos adjuntos y continuaré de inmediato.",
         "it": "Risponda a questa e-mail con i documenti in allegato e proseguirò subito.",
         "nl": "Beantwoord deze e-mail met de documenten als bijlage, dan ga ik direct verder."},
    ),
    (
        r"Your message describes the cargo as non-dangerous, but it also mentions "
        r"(?P<signals>.+)\. Could you confirm which is correct\?",
        {"de": "Ihre Nachricht beschreibt die Ware als nicht gefährlich, erwähnt aber auch "
         "{signals}. Können Sie bestätigen, was zutrifft?",
         "fr": "Votre message décrit la marchandise comme non dangereuse, mais mentionne aussi "
         "{signals}. Pouvez-vous confirmer ce qui est exact ?",
         "es": "Su mensaje describe la carga como no peligrosa, pero también menciona "
         "{signals}. ¿Puede confirmar qué es correcto?",
         "it": "Il suo messaggio descrive la merce come non pericolosa, ma menziona anche "
         "{signals}. Può confermare cosa è corretto?",
         "nl": "Uw bericht beschrijft de lading als niet-gevaarlijk, maar vermeldt ook "
         "{signals}. Kunt u bevestigen wat juist is?"},
    ),
    (
        r"If it is dangerous goods, please attach (?P<terms>.+)\. If it is not, a short "
        r"confirmation is enough and I will quote straight away\.",
        {"de": "Falls es Gefahrgut ist, senden Sie bitte {terms} mit. Falls nicht, genügt "
         "eine kurze Bestätigung und ich erstelle sofort das Angebot.",
         "fr": "S'il s'agit de marchandises dangereuses, merci de joindre {terms}. Sinon, une "
         "courte confirmation suffit et je vous fais une offre tout de suite.",
         "es": "Si es mercancía peligrosa, adjunte {terms}. Si no lo es, basta con una breve "
         "confirmación y le enviaré la oferta de inmediato.",
         "it": "Se si tratta di merce pericolosa, alleghi {terms}. In caso contrario basta una "
         "breve conferma e le invio subito l'offerta.",
         "nl": "Als het gevaarlijke goederen zijn, voeg dan {terms} bij. Zo niet, dan volstaat "
         "een korte bevestiging en stuur ik direct een offerte."},
    ),
    (
        r"I am sorry, I cannot offer a price right now: (?P<reason>.+)\. I cannot say yet "
        r"when a rate will be available\.",
        {"de": "Leider kann ich Ihnen im Moment keinen Preis anbieten ({reason}). Ich kann "
         "noch nicht sagen, wann ein Tarif verfügbar ist.",
         "fr": "Je suis désolé, je ne peux pas vous proposer de prix pour le moment "
         "({reason}). Je ne sais pas encore quand un tarif sera disponible.",
         "es": "Lo siento, ahora mismo no puedo ofrecerle un precio ({reason}). Todavía no sé "
         "cuándo habrá una tarifa disponible.",
         "it": "Mi dispiace, al momento non posso offrirle un prezzo ({reason}). Non so ancora "
         "quando sarà disponibile una tariffa.",
         "nl": "Het spijt me, ik kan u op dit moment geen prijs aanbieden ({reason}). Ik weet "
         "nog niet wanneer er een tarief beschikbaar is."},
    ),
    (
        r"If anything about the shipment can change, tell me and I will check again\.",
        {"de": "Falls sich an der Sendung etwas ändern kann, sagen Sie mir Bescheid und ich "
         "prüfe erneut.",
         "fr": "Si quelque chose peut changer dans l'envoi, dites-le-moi et je vérifierai à "
         "nouveau.",
         "es": "Si algo del envío puede cambiar, dígamelo y lo revisaré de nuevo.",
         "it": "Se qualcosa della spedizione può cambiare, me lo dica e verificherò di nuovo.",
         "nl": "Als er iets aan de zending kan veranderen, laat het me weten en ik kijk "
         "opnieuw."},
    ),
    (
        r"Thank you for accepting\. I have submitted the booking with (?P<carrier>.+) under "
        r"our reference (?P<ref>\S+)\.",
        {"de": "Vielen Dank für Ihre Zusage. Ich habe die Buchung bei {carrier} unter unserer "
         "Referenz {ref} eingereicht.",
         "fr": "Merci pour votre accord. J'ai transmis la réservation à {carrier} sous notre "
         "référence {ref}.",
         "es": "Gracias por aceptar. He enviado la reserva a {carrier} con nuestra referencia "
         "{ref}.",
         "it": "Grazie per l'accettazione. Ho inoltrato la prenotazione a {carrier} con il "
         "nostro riferimento {ref}.",
         "nl": "Dank u voor uw akkoord. Ik heb de boeking bij {carrier} ingediend onder onze "
         "referentie {ref}."},
    ),
    (
        r"It is not confirmed yet\. I will write again as soon as the carrier confirms it\.",
        {"de": "Sie ist noch nicht bestätigt. Ich melde mich wieder, sobald die "
         "Fluggesellschaft sie bestätigt.",
         "fr": "Elle n'est pas encore confirmée. Je vous écrirai dès que la compagnie l'aura "
         "confirmée.",
         "es": "Todavía no está confirmada. Le escribiré en cuanto la aerolínea la confirme.",
         "it": "Non è ancora confermata. Le scriverò appena la compagnia la conferma.",
         "nl": "Deze is nog niet bevestigd. Ik schrijf u opnieuw zodra de "
         "luchtvaartmaatschappij hem bevestigt."},
    ),
    (
        r"Your booking is confirmed\. Booking reference: (?P<ref>\S+)\.",
        {"de": "Ihre Buchung ist bestätigt. Buchungsreferenz: {ref}.",
         "fr": "Votre réservation est confirmée. Référence de réservation : {ref}.",
         "es": "Su reserva está confirmada. Referencia de reserva: {ref}.",
         "it": "La sua prenotazione è confermata. Riferimento di prenotazione: {ref}.",
         "nl": "Uw boeking is bevestigd. Boekingsreferentie: {ref}."},
    ),
    (
        r"(?P<w>\S+) kg" + _R + r" with (?P<carrier>.+), (?P<cur>\S+) (?P<total>\S+) all-in\.",
        {"de": "{w} kg{route} mit {carrier}, {cur} {total}, alles inklusive.",
         "fr": "{w} kg{route} avec {carrier}, {cur} {total}, tout compris.",
         "es": "{w} kg{route} con {carrier}, {cur} {total}, todo incluido.",
         "it": "{w} kg{route} con {carrier}, {cur} {total}, tutto incluso.",
         "nl": "{w} kg{route} met {carrier}, {cur} {total}, alles inbegrepen."},
    ),
    (
        r"I will pass on tracking updates as I receive them\.",
        {"de": "Updates zur Sendungsverfolgung leite ich weiter, sobald ich sie erhalte.",
         "fr": "Je vous transmettrai les informations de suivi dès que je les recevrai.",
         "es": "Le enviaré las actualizaciones de seguimiento en cuanto las reciba.",
         "it": "Le inoltrerò gli aggiornamenti di tracciamento appena li ricevo.",
         "nl": "Ik stuur trackingupdates door zodra ik ze ontvang."},
    ),
    (
        r"I am sorry, I could not complete the booking with (?P<carrier>.+?): "
        r"(?P<reason>.+)\.",
        {"de": "Leider konnte ich die Buchung bei {carrier} nicht abschließen ({reason}).",
         "fr": "Je suis désolé, je n'ai pas pu finaliser la réservation avec {carrier} "
         "({reason}).",
         "es": "Lo siento, no he podido completar la reserva con {carrier} ({reason}).",
         "it": "Mi dispiace, non sono riuscito a completare la prenotazione con {carrier} "
         "({reason}).",
         "nl": "Het spijt me, ik kon de boeking bij {carrier} niet afronden ({reason})."},
    ),
    (
        r"Nothing has been booked\. If you still want to ship, reply to this email and I will "
        r"send you an updated offer\.",
        {"de": "Es wurde nichts gebucht. Wenn Sie weiterhin versenden möchten, antworten Sie "
         "auf diese E-Mail und ich sende Ihnen ein aktualisiertes Angebot.",
         "fr": "Rien n'a été réservé. Si vous souhaitez toujours expédier, répondez à cet "
         "e-mail et je vous enverrai une offre actualisée.",
         "es": "No se ha reservado nada. Si todavía desea enviar, responda a este correo y le "
         "enviaré una oferta actualizada.",
         "it": "Non è stato prenotato nulla. Se desidera ancora spedire, risponda a questa "
         "e-mail e le invierò un'offerta aggiornata.",
         "nl": "Er is niets geboekt. Als u toch wilt verzenden, beantwoord deze e-mail en ik "
         "stuur u een bijgewerkte offerte."},
    ),
    (
        r"Thank you for your reply\. I have not booked anything yet, because (?P<terms>.+)\.",
        {"de": "Vielen Dank für Ihre Antwort. Ich habe noch nichts gebucht, weil {terms}.",
         "fr": "Merci pour votre réponse. Je n'ai encore rien réservé, car {terms}.",
         "es": "Gracias por su respuesta. Todavía no he reservado nada, porque {terms}.",
         "it": "Grazie per la sua risposta. Non ho ancora prenotato nulla, perché {terms}.",
         "nl": "Dank u voor uw antwoord. Ik heb nog niets geboekt, omdat {terms}."},
    ),
    (
        r"If you accept the offer with (?P<carrier>.+) exactly as quoted, please reply "
        r"\"We accept the quote\" and I will book it\. If you need a change, tell me what you "
        r"need and I will send a revised offer\.",
        {"de": "Wenn Sie das Angebot mit {carrier} genau so annehmen, antworten Sie bitte "
         "\"Wir akzeptieren das Angebot\" und ich buche es. Wenn Sie eine Änderung brauchen, "
         "sagen Sie mir welche, und ich sende Ihnen ein überarbeitetes Angebot.",
         "fr": "Si vous acceptez l'offre avec {carrier} telle quelle, répondez \"Nous "
         "acceptons l'offre\" et je la réserve. Si vous avez besoin d'un changement, dites-moi "
         "lequel et je vous enverrai une offre révisée.",
         "es": "Si acepta la oferta con {carrier} tal como está, responda \"Aceptamos la "
         "oferta\" y la reservaré. Si necesita un cambio, dígame cuál y le enviaré una oferta "
         "revisada.",
         "it": "Se accetta l'offerta con {carrier} così com'è, risponda \"Accettiamo "
         "l'offerta\" e la prenoterò. Se le serve una modifica, mi dica quale e le invierò "
         "un'offerta rivista.",
         "nl": "Als u het aanbod met {carrier} precies zo accepteert, antwoord dan \"Wij "
         "accepteren het aanbod\" en ik boek het. Als u een wijziging nodig heeft, laat me "
         "weten welke en ik stuur u een herziene offerte."},
    ),
    (
        r"Your shipment of (?P<w>\S+) kg" + _R + r" is booked and confirmed with "
        r"(?P<carrier>.+), booking reference (?P<ref>\S+)\.",
        {"de": "Ihre Sendung mit {w} kg{route} ist bei {carrier} gebucht und bestätigt, "
         "Buchungsreferenz {ref}.",
         "fr": "Votre envoi de {w} kg{route} est réservé et confirmé auprès de {carrier}, "
         "référence de réservation {ref}.",
         "es": "Su envío de {w} kg{route} está reservado y confirmado con {carrier}, "
         "referencia de reserva {ref}.",
         "it": "La sua spedizione di {w} kg{route} è prenotata e confermata con {carrier}, "
         "riferimento di prenotazione {ref}.",
         "nl": "Uw zending van {w} kg{route} is geboekt en bevestigd bij {carrier}, "
         "boekingsreferentie {ref}."},
    ),
    (
        r"It is ready on (?P<when>\S+), so it has not been collected or flown yet\.",
        {"de": "Sie ist ab {when} versandbereit und wurde daher noch nicht abgeholt oder "
         "geflogen.",
         "fr": "Il est prêt le {when} ; il n'a donc pas encore été enlevé ni expédié.",
         "es": "Está listo el {when}, por lo que todavía no se ha recogido ni volado.",
         "it": "È pronta il {when}, quindi non è ancora stata ritirata né spedita.",
         "nl": "Deze is gereed op {when} en is dus nog niet opgehaald of gevlogen."},
    ),
    (
        r"I have not received a tracking update for it yet, so it has not been reported as "
        r"collected or departed\.",
        {"de": "Ich habe noch keine Statusmeldung erhalten; sie wurde also noch nicht als "
         "abgeholt oder abgeflogen gemeldet.",
         "fr": "Je n'ai pas encore reçu de mise à jour de suivi ; il n'a donc pas été signalé "
         "comme enlevé ou parti.",
         "es": "Todavía no he recibido ninguna actualización de seguimiento, así que no consta "
         "como recogido ni salido.",
         "it": "Non ho ancora ricevuto aggiornamenti di tracciamento, quindi non risulta "
         "ritirata né partita.",
         "nl": "Ik heb nog geen trackingupdate ontvangen, dus deze is nog niet als opgehaald of "
         "vertrokken gemeld."},
    ),
    (
        r"I will pass on the next update as soon as I receive it\.",
        {"de": "Die nächste Meldung leite ich weiter, sobald ich sie erhalte.",
         "fr": "Je vous transmettrai la prochaine mise à jour dès que je la recevrai.",
         "es": "Le enviaré la próxima actualización en cuanto la reciba.",
         "it": "Le inoltrerò il prossimo aggiornamento appena lo ricevo.",
         "nl": "Ik stuur de volgende update door zodra ik die ontvang."},
    ),
    (
        r"Your booking with (?P<carrier>.+) has been submitted but is not confirmed yet, so "
        r"there is no shipment status to report\.",
        {"de": "Ihre Buchung bei {carrier} ist eingereicht, aber noch nicht bestätigt, daher "
         "gibt es noch keinen Sendungsstatus.",
         "fr": "Votre réservation auprès de {carrier} a été transmise mais n'est pas encore "
         "confirmée ; il n'y a donc pas encore de statut d'envoi.",
         "es": "Su reserva con {carrier} se ha enviado pero aún no está confirmada, así que "
         "todavía no hay estado del envío.",
         "it": "La sua prenotazione con {carrier} è stata inoltrata ma non è ancora confermata, "
         "quindi non c'è ancora uno stato della spedizione.",
         "nl": "Uw boeking bij {carrier} is ingediend maar nog niet bevestigd, dus er is nog "
         "geen zendingsstatus."},
    ),
    (
        r"I will write as soon as the carrier confirms it\.",
        {"de": "Ich melde mich, sobald die Fluggesellschaft sie bestätigt.",
         "fr": "Je vous écrirai dès que la compagnie l'aura confirmée.",
         "es": "Le escribiré en cuanto la aerolínea la confirme.",
         "it": "Le scriverò appena la compagnia la conferma.",
         "nl": "Ik schrijf u zodra de luchtvaartmaatschappij hem bevestigt."},
    ),
    (
        r"Thank you for your question\.",
        {"de": "Vielen Dank für Ihre Frage.", "fr": "Merci pour votre question.",
         "es": "Gracias por su pregunta.", "it": "Grazie per la sua domanda.",
         "nl": "Dank u voor uw vraag."},
    ),
    (
        r"The total in my quote is all-in\. It covers (?P<terms>.+), and nothing comes on top\. "
        r"Pickup from (?P<pf>.+) to (?P<o>\S+) airport is included\.",
        {"de": "Der Gesamtpreis in meinem Angebot ist all-in. Er umfasst {terms}, und es kommt "
         "nichts hinzu. Die Abholung ab {pf} zum Flughafen {o} ist enthalten.",
         "fr": "Le total de mon offre est tout compris. Il couvre {terms}, et rien ne s'y "
         "ajoute. L'enlèvement depuis {pf} jusqu'à l'aéroport de {o} est inclus.",
         "es": "El total de mi oferta es todo incluido. Cubre {terms} y no se añade nada más. "
         "La recogida desde {pf} hasta el aeropuerto de {o} está incluida.",
         "it": "Il totale della mia offerta è tutto incluso. Comprende {terms} e non si "
         "aggiunge nulla. Il ritiro da {pf} all'aeroporto di {o} è incluso.",
         "nl": "Het totaal in mijn offerte is all-in. Het omvat {terms} en er komt niets bij. "
         "Ophalen vanaf {pf} naar luchthaven {o} is inbegrepen."},
    ),
    (
        r"The total in my quote is all-in\. It covers (?P<terms>.+), and nothing comes on top\. "
        r"Pickup is not included: the price starts at (?P<o>\S+) airport\.",
        {"de": "Der Gesamtpreis in meinem Angebot ist all-in. Er umfasst {terms}, und es kommt "
         "nichts hinzu. Die Abholung ist nicht enthalten: Der Preis gilt ab Flughafen {o}.",
         "fr": "Le total de mon offre est tout compris. Il couvre {terms}, et rien ne s'y "
         "ajoute. L'enlèvement n'est pas inclus : le prix s'entend au départ de l'aéroport de "
         "{o}.",
         "es": "El total de mi oferta es todo incluido. Cubre {terms} y no se añade nada más. "
         "La recogida no está incluida: el precio es desde el aeropuerto de {o}.",
         "it": "Il totale della mia offerta è tutto incluso. Comprende {terms} e non si "
         "aggiunge nulla. Il ritiro non è incluso: il prezzo parte dall'aeroporto di {o}.",
         "nl": "Het totaal in mijn offerte is all-in. Het omvat {terms} en er komt niets bij. "
         "Ophalen is niet inbegrepen: de prijs geldt vanaf luchthaven {o}."},
    ),
    (
        r"(?P<carrier>.+) needs (?P<days>\d+) days in transit, and the offer is valid until "
        r"(?P<when>.+)\.",
        {"de": "{carrier} benötigt {days} Tage Transitzeit, und das Angebot gilt bis {when}.",
         "fr": "{carrier} a besoin de {days} jours de transit, et l'offre est valable "
         "jusqu'au {when}.",
         "es": "{carrier} necesita {days} días de tránsito, y la oferta es válida hasta "
         "{when}.",
         "it": "{carrier} impiega {days} giorni di transito, e l'offerta è valida fino al "
         "{when}.",
         "nl": "{carrier} heeft {days} dagen transittijd nodig, en het aanbod is geldig tot "
         "{when}."},
    ),
    (
        r"(?P<carrier>.+) needs (?P<days>\d+) days in transit\.",
        {"de": "{carrier} benötigt {days} Tage Transitzeit.",
         "fr": "{carrier} a besoin de {days} jours de transit.",
         "es": "{carrier} necesita {days} días de tránsito.",
         "it": "{carrier} impiega {days} giorni di transito.",
         "nl": "{carrier} heeft {days} dagen transittijd nodig."},
    ),
    (
        r"(?P<carrier>.+) has not confirmed a transit time, and the offer is valid until "
        r"(?P<when>.+)\.",
        {"de": "{carrier} hat noch keine Transitzeit bestätigt, und das Angebot gilt bis "
         "{when}.",
         "fr": "{carrier} n'a pas encore confirmé de délai de transit, et l'offre est valable "
         "jusqu'au {when}.",
         "es": "{carrier} todavía no ha confirmado el tiempo de tránsito, y la oferta es "
         "válida hasta {when}.",
         "it": "{carrier} non ha ancora confermato il tempo di transito, e l'offerta è valida "
         "fino al {when}.",
         "nl": "{carrier} heeft nog geen transittijd bevestigd, en het aanbod is geldig tot "
         "{when}."},
    ),
    (
        r"(?P<carrier>.+) has not confirmed a transit time\.",
        {"de": "{carrier} hat noch keine Transitzeit bestätigt.",
         "fr": "{carrier} n'a pas encore confirmé de délai de transit.",
         "es": "{carrier} todavía no ha confirmado el tiempo de tránsito.",
         "it": "{carrier} non ha ancora confermato il tempo di transito.",
         "nl": "{carrier} heeft nog geen transittijd bevestigd."},
    ),
]
_COMPILED = [(re.compile(pattern), out) for pattern, out in _LINES]


def _route(lang: str, route: str) -> str:
    both, start, end = _ROUTE[lang]
    if m := re.fullmatch(r" from (\S+) to (\S+)", route):
        return both.format(o=m[1], d=m[2])
    if m := re.fullmatch(r" from (\S+)", route):
        return start.format(o=m[1])
    if m := re.fullmatch(r" to (\S+)", route):
        return end.format(d=m[1])
    return route


def _terms(lang: str, text: str) -> str:
    for english, local in _TERMS.items():
        text = text.replace(english, local[lang])
    return text.replace(" and ", f" {_AND[lang]} ")


def _line(lang: str, line: str) -> str:
    for pattern, out in _COMPILED:
        if m := pattern.fullmatch(line):
            values = m.groupdict()
            if "route" in values:
                values["route"] = _route(lang, values["route"] or "")
            if "terms" in values:
                values["terms"] = _terms(lang, values["terms"])
            if "signals" in values:
                values["signals"] = values["signals"].replace(" and ", f" {_AND[lang]} ")
            return out[lang].format(**values)
    return line


def localize(body: str, lang: str) -> str:
    """`body` (a replies.py customer mail) in `lang`; English if anything would get lost."""
    if lang not in _ROUTE:
        return body
    local = "\n".join(_line(lang, line) for line in body.split("\n"))
    kept = all(n in _NUMBER.findall(local) for n in _NUMBER.findall(body))
    return local if kept else body
