<?php

namespace Modules\Monitoring\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Routing\Controller;

class ParameterKpiController extends Controller
{
    /**
     * Calculate KPI metrics for a parameter over a given period.
     *
     * Initial stub: Needs FormRequest validation via ParameterKpiRequest
     * and calculation logic returning status, value, and deviation.
     *
     * @param Request $request
     * @return JsonResponse
     */
    public function calculate(Request $request): JsonResponse
    {
        // TODO: Validate input via ParameterKpiRequest
        // TODO: Compute value and deviation for parameter_id and period
        return response()->json([
            'status' => 'incomplete',
            'value' => 0.0,
            'deviation' => 0.0,
        ], 501);
    }
}
