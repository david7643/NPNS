import unittest
from pydantic import ValidationError
from app.schemas import ContactCreate, SavedContactUpdate


class ContactValidationTests(unittest.TestCase):
    def test_whitespace_is_rejected_before_insert_and_update(self):
        for model in (ContactCreate, SavedContactUpdate):
            for blank_field in ('name', 'message'):
                with self.subTest(model=model.__name__, field=blank_field):
                    body = dict(name='Emergency', phone='01012345678', message='Test')
                    body[blank_field] = '   '
                    with self.assertRaises(ValidationError): model(**body)

    def test_valid_contact_is_normalized_without_changing_phone_contract(self):
        body = ContactCreate(name=' Emergency ', phone=' 010-1234-5678 ', message=' Test ')
        self.assertEqual(body.name, 'Emergency')
        self.assertEqual(body.message, 'Test')
        self.assertEqual(body.phone, '010-1234-5678')
