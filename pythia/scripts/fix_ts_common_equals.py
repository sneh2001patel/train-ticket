from pathlib import Path

order_path = Path("ts-common/src/main/java/edu/fudan/common/entity/Order.java")
text = order_path.read_text()

text = text.replace(
    "return getBoughtDate().equals(other.getBoughtDate())\n"
    "                && getBoughtDate().equals(other.getTravelDate())\n"
    "                && getTravelTime().equals(other.getTravelTime())\n"
    "                && accountId .equals( other.getAccountId() )\n"
    "                && contactsName.equals(other.getContactsName())\n"
    "                && contactsDocumentNumber.equals(other.getContactsDocumentNumber())\n"
    "                && documentType == other.getDocumentType()\n"
    "                && trainNumber.equals(other.getTrainNumber())\n"
    "                && coachNumber == other.getCoachNumber()\n"
    "                && seatClass == other.getSeatClass()\n"
    "                && seatNumber .equals(other.getSeatNumber())\n"
    "                && from.equals(other.getFrom())\n"
    "                && to.equals(other.getTo())\n"
    "                && status == other.getStatus()\n"
    "                && price.equals(other.price);",
    "return boughtDate.equals(other.boughtDate)\n"
    "                && travelDate.equals(other.travelDate)\n"
    "                && travelTime.equals(other.travelTime)\n"
    "                && accountId.equals(other.accountId)\n"
    "                && contactsName.equals(other.contactsName)\n"
    "                && contactsDocumentNumber.equals(other.contactsDocumentNumber)\n"
    "                && documentType == other.documentType\n"
    "                && trainNumber.equals(other.trainNumber)\n"
    "                && coachNumber == other.coachNumber\n"
    "                && seatClass == other.seatClass\n"
    "                && seatNumber.equals(other.seatNumber)\n"
    "                && from.equals(other.from)\n"
    "                && to.equals(other.to)\n"
    "                && status == other.status\n"
    "                && price.equals(other.price);",
)
order_path.write_text(text)

contacts_path = Path("ts-common/src/main/java/edu/fudan/common/entity/Contacts.java")
text = contacts_path.read_text()
text = text.replace(
    "return name.equals(other.getName())\n"
    "                && accountId .equals( other.getAccountId() )\n"
    "                && documentNumber.equals(other.getDocumentNumber())\n"
    "                && phoneNumber.equals(other.getPhoneNumber())\n"
    "                && documentType == other.getDocumentType();",
    "return name.equals(other.name)\n"
    "                && accountId.equals(other.accountId)\n"
    "                && documentNumber.equals(other.documentNumber)\n"
    "                && phoneNumber.equals(other.phoneNumber)\n"
    "                && documentType == other.documentType;",
)
contacts_path.write_text(text)

print("patched")
