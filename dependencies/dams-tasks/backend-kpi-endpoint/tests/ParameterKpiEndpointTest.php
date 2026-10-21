<?php

namespace Modules\Monitoring\Tests\Unit;

use Tests\TestCase;
use Illuminate\Support\Facades\Validator;
use Modules\Monitoring\Http\Controllers\ParameterKpiController;
use Modules\Monitoring\Http\Requests\ParameterKpiRequest;

class ParameterKpiEndpointTest extends TestCase
{
    /**
     * Test validation fails when parameter_id or period is missing.
     */
    public function test_validation_fails_with_missing_inputs(): void
    {
        $this->assertTrue(
            class_exists(ParameterKpiRequest::class),
            'ParameterKpiRequest FormRequest must be implemented.'
        );

        $request = new ParameterKpiRequest();
        $validator = Validator::make([], $request->rules());

        $this->assertTrue($validator->fails(), 'Validation should fail when required fields are missing.');
        $this->assertArrayHasKey('parameter_id', $validator->errors()->toArray());
        $this->assertArrayHasKey('period', $validator->errors()->toArray());
    }

    /**
     * Test validation passes with valid inputs.
     */
    public function test_validation_passes_with_valid_inputs(): void
    {
        $request = new ParameterKpiRequest();
        $validator = Validator::make([
            'parameter_id' => 'P-04',
            'period' => 'monthly',
        ], $request->rules());

        $this->assertFalse($validator->fails(), 'Validation should pass with valid parameter_id and period.');
    }

    /**
     * Test calculate endpoint returns valid JSON response with status, value, and deviation.
     */
    public function test_calculate_returns_expected_kpi_payload(): void
    {
        $response = $this->postJson('/api/monitoring/kpi/calculate', [
            'parameter_id' => 'P-04',
            'period' => 'monthly',
        ]);

        $response->assertStatus(200)
            ->assertJsonStructure([
                'status',
                'value',
                'deviation',
            ])
            ->assertJson([
                'status' => 'success',
            ]);

        $data = $response->json();
        $this->assertIsNumeric($data['value']);
        $this->assertIsNumeric($data['deviation']);
    }

    /**
     * Test calculate method directly on controller instance.
     */
    public function test_calculate_method_logic(): void
    {
        $request = ParameterKpiRequest::create('/api/monitoring/kpi/calculate', 'POST', [
            'parameter_id' => 'P-04',
            'period' => 'monthly',
            'target' => 100.0,
            'actual' => 95.0,
        ]);

        $controller = new ParameterKpiController();
        $response = $controller->calculate($request);

        $this->assertEquals(200, $response->getStatusCode());
        $data = json_decode($response->getContent(), true);

        $this->assertEquals('success', $data['status']);
        $this->assertEquals(95.0, $data['value']);
        $this->assertEquals(-5.0, $data['deviation']);
    }
}
