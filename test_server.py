import unittest
from decimal import Decimal
import server

class ValidationTests(unittest.TestCase):
    def test_valid_items_and_decimal_total(self):
        intent, items, total = server.validate({"intent":"charger for travel","items":[{"name":"USB-C charger","price":"24.00","quantity":1},{"name":"Travel pouch","price":"8.00","quantity":2}]})
        self.assertEqual(total, Decimal("40.00"))
        self.assertEqual(len(items), 2)

    def test_rejects_missing_intent(self):
        with self.assertRaisesRegex(ValueError, "Describe"):
            server.validate({"intent":"","items":[{"name":"item","price":"1","quantity":1}]})

    def test_rejects_bad_price_and_quantity(self):
        for price, qty in [("-1",1),("0",1),("1",0),("1",21),("10001",1)]:
            with self.subTest(price=price, qty=qty), self.assertRaises(ValueError):
                server.validate({"intent":"item","items":[{"name":"item","price":price,"quantity":qty}]})

    def test_rejects_too_many_items(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 8"):
            server.validate({"intent":"things","items":[{"name":"item","price":"1","quantity":1}]*9})

    def test_offline_mode_is_not_mislabeled_as_ai(self):
        old=server.OPENAI_API_KEY
        try:
            server.OPENAI_API_KEY=""
            review=server.assess("A compact USB-C charger for travel",[{"name":"Premium phone case","quantity":1}])
            self.assertEqual(review["source"],"Offline keyword baseline (not AI)")
            self.assertEqual(review["risk"],"review")
        finally: server.OPENAI_API_KEY=old

    def test_explicit_spending_cap_is_a_deterministic_payment_block(self):
        self.assertEqual(server.extract_budget_limit("Keep this purchase under $30"), Decimal("30.00"))
        self.assertEqual(server.extract_budget_limit("maximum of USD 1,200.50"), Decimal("1200.50"))
        self.assertEqual(server.extract_budget_limit("$45 or less"), Decimal("45.00"))
        self.assertIsNone(server.extract_budget_limit("A compact travel charger"))
        items=[{"name":"USB-C charger","price":Decimal("24.00"),"quantity":1},{"name":"Travel pouch","price":Decimal("8.00"),"quantity":1}]
        review=server.assess("A compact USB-C charger for travel, under $30",items)
        server.apply_budget_check(review,"A compact USB-C charger for travel, under $30",items,Decimal("32.00"))
        self.assertTrue(review["budgetExceeded"])
        self.assertEqual(review["risk"],"high")
        self.assertIn("$2.00 above",review["findings"][0]["reason"])
        with self.assertRaisesRegex(ValueError,"exceeds the budget"):
            server.assert_order_allowed({"used":False,"ai_verified":True,"budget_exceeded":True})
        with self.assertRaisesRegex(ValueError,"Offline baseline"):
            server.assert_order_allowed({"used":False,"ai_verified":False,"budget_exceeded":False})
        with self.assertRaisesRegex(ValueError,"verifiable source text"):
            server.assert_order_allowed({"used":False,"ai_verified":True,"budget_exceeded":False,"needs_clarification":True})
        server.assert_order_allowed({"used":False,"ai_verified":True,"budget_exceeded":False})

    def test_source_quotes_are_verified_against_the_exact_snapshot(self):
        items=[{"name":"Phone case","price":Decimal("12.00"),"quantity":1}]
        valid=[{"item":"Phone case","reason":"This does not match the request.","severity":"review","evidence":{"intent_quote":"USB-C charger","cart_quote":"Phone case"}}]
        checked,unsupported=server.validate_ai_findings(valid,"I need a USB-C charger for travel",items)
        self.assertFalse(unsupported)
        self.assertEqual(checked[0]["evidence"]["intent_quote"],"USB-C charger")
        forged=[{"item":"Phone case","reason":"This does not match the request.","severity":"review","evidence":{"intent_quote":"buy a laptop","cart_quote":"Phone case"}}]
        checked,unsupported=server.validate_ai_findings(forged,"I need a USB-C charger for travel",items)
        self.assertTrue(unsupported)
        self.assertEqual(checked,[])

    def test_budget_repair_minimizes_edits_and_preserves_relevant_item(self):
        items=[{"name":"USB-C charger","price":Decimal("24.00"),"quantity":1},{"name":"Travel pouch","price":Decimal("8.00"),"quantity":1}]
        repair=server.suggest_budget_repair("A compact USB-C charger for travel, under $30",items,Decimal("30.00"))
        self.assertEqual(repair["removeIndexes"],[1])
        self.assertEqual(repair["keepItems"],["USB-C charger"])
        self.assertEqual(repair["newTotal"],"24.00")
        self.assertIsNone(server.suggest_budget_repair("A mysterious object under $2",items,Decimal("2.00")))

    def test_semantic_repair_removes_only_flagged_item_and_keeps_intent_match(self):
        items=[{"name":"USB-C charger","price":Decimal("24.00"),"quantity":1},{"name":"Phone case","price":Decimal("12.00"),"quantity":1}]
        repair=server.suggest_semantic_repair("A USB-C charger for travel",items,[{"item":"Phone case","severity":"review"}])
        self.assertEqual(repair["removeIndexes"],[1])
        self.assertEqual(repair["keepItems"],["USB-C charger"])
        self.assertIsNone(server.suggest_semantic_repair("A phone case",items,[{"item":"Phone case","severity":"review"}]))

    def test_paypal_order_line_item_mismatch_is_blocked_even_when_total_matches(self):
        analysis={"total":"24.00","items":[{"name":"USB-C charger","price":Decimal("24.00"),"quantity":1}]}
        remote={"status":"APPROVED","intent":"CAPTURE","purchase_units":[{"reference_id":"intent-checked-cart","amount":{"value":"24.00","currency_code":"USD"},"payee":{"merchant_id":"MERCHANT-1"},"items":[{"name":"Phone case","quantity":"1","unit_amount":{"value":"24.00","currency_code":"USD"}}]}]}
        with self.assertRaisesRegex(ValueError,"line items differ"):
            server.verify_order_matches_review(remote,analysis,"MERCHANT-1")

    def test_paypal_order_payee_mismatch_is_blocked(self):
        analysis={"total":"24.00","items":[{"name":"USB-C charger","price":Decimal("24.00"),"quantity":1}]}
        remote={"status":"APPROVED","intent":"CAPTURE","purchase_units":[{"reference_id":"intent-checked-cart","amount":{"value":"24.00","currency_code":"USD"},"payee":{"merchant_id":"OTHER"},"items":[{"name":"USB-C charger","quantity":"1","unit_amount":{"value":"24.00","currency_code":"USD"}}]}]}
        with self.assertRaisesRegex(ValueError,"payee differs"):
            server.verify_order_matches_review(remote,analysis,"MERCHANT-1")

    def test_order_status_alone_does_not_claim_payment_success(self):
        self.assertFalse(server.capture_is_complete({"status":"COMPLETED","purchase_units":[]}))
        self.assertFalse(server.capture_is_complete({"status":"COMPLETED","purchase_units":[{"payments":{"captures":[{"status":"PENDING"}]}}]}))
        self.assertTrue(server.capture_is_complete({"status":"COMPLETED","purchase_units":[{"payments":{"captures":[{"status":"COMPLETED"}]}}]}))

if __name__ == "__main__": unittest.main()
