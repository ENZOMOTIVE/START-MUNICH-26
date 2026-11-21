from app.workflows import language, replies

FRENCH_RFQ = (  # live round 70, mail 19533
    "Bonjour,\n\nJ’espère que tout va bien de votre côté. J’ai un envoi de pièces détachées pour "
    "machines d’emballage à faire partir vers Sydney, AU (SYD). Il s’agit de 9 colis, pour un "
    "poids brut total de 279 kg et un volume de 2.47 cbm.\n\nMerci d’avance et bonne journée !"
)
GERMAN_RFQ = (  # live round 71, mail 19877
    "Guten Tag,\n\nbitte bieten Sie uns die günstigste Luftfrachtoption nach RUH (Riyadh, SA) an. "
    "Es handelt sich um 4 Packstücke mit gedrucktem Werbematerial, einem Bruttogewicht von "
    "990 kg und einem Volumen von 7.29 cbm.\n\nVielen Dank."
)


def test_detects_the_customer_language():
    assert language.detect(FRENCH_RFQ) == "fr"
    assert language.detect(GERMAN_RFQ) == "de"
    english = "Hello, please quote 120 kg FRA to JFK. Thank you for your help."
    assert language.detect(english) == "en"


def test_quote_is_localized_with_every_number_kept():
    mail = replies.final_quote(
        None, "Devis", "DUS", "SYD", "279", "Cobalt Skylift", "1472.59", "EUR", 4,
        ["air_freight", "fuel", "security"], None,
    )
    french = language.localize(mail["body"], "fr")
    assert "Voici notre offre pour 279 kg de DUS à SYD" in french
    assert "Total : EUR 1472.59, tout compris" in french
    assert "Thank you" not in french
    assert language.localize(mail["body"], "en") == mail["body"]
